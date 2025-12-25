import os
import re
import threading
import time
from datetime import datetime
from typing import Dict, Optional, Union
from urllib.parse import quote

import telebot
from dotenv import load_dotenv
from telebot import types

from macro_engine import build_macro_drafts, create_infographic, generate_infographic_image

load_dotenv()


def _get_chat_id(raw_chat_id: Optional[str]) -> Optional[Union[int, str]]:
    if not raw_chat_id:
        return None
    value = raw_chat_id.strip()
    if value.lstrip("-").isdigit():
        return int(value)
    return value


def _sanitize_markdown_code(text: str) -> str:
    return text.replace("```", "'''").replace("`", "'")


def _normalize_intent_text(text: str) -> str:
    # Collapse odd whitespace/newlines that can break URL decoding in some clients.
    normalized = " ".join(text.split())
    # Escape literal % so it survives clients that double-decode URL parameters.
    return normalized.replace("%", "%25")


def _extract_intent_hashtags(text: str, max_count: int = 2) -> tuple[str, list[str]]:
    tags = re.findall(r"#([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9_]+)", text or "")
    unique: list[str] = []
    seen = set()
    for tag in tags:
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(tag)
        if len(unique) >= max_count:
            break

    if not unique:
        return (text or "").strip(), []

    cleaned = re.sub(r"#([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9_]+)", "", text or "")
    cleaned = " ".join(cleaned.split()).strip()
    return cleaned, unique


def _strip_part_labels(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"(?im)^\s*parte\s*[12]\s*(?:\([^)]*\))?\s*:\s*", "", cleaned)
    cleaned = re.sub(r"(?im)^\s*an[aá]lisis\s*:?\s*", "", cleaned)
    return cleaned.strip()


def _split_ai_response(ai_text: str) -> tuple[str, str]:
    raw = (ai_text or "").strip()
    if not raw:
        return "", ""
    parts = raw.split("###", 1)
    part1 = _strip_part_labels(parts[0])
    part2 = _strip_part_labels(parts[1]) if len(parts) > 1 else ""
    return part1, part2


TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = _get_chat_id(os.getenv("TELEGRAM_CHAT_ID"))

if not TELEGRAM_TOKEN or TELEGRAM_CHAT_ID is None:
    raise RuntimeError("Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID en .env")

bot = telebot.TeleBot(TELEGRAM_TOKEN)

pending_posts: Dict[int, str] = {}
IMAGE_OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "temp_plot.png")


def send_drafts(drafts, chat_id):
    for index, draft in enumerate(drafts, start=1):
        if isinstance(draft, dict):
            ai_text = (draft.get("ai_text") or draft.get("tweet_text") or draft.get("draft") or "").strip()
            title_text = (draft.get("title") or "").strip()
        else:
            ai_text = (draft or "").strip()
            title_text = ""

        if not ai_text:
            continue

        image_text, post_text = _split_ai_response(ai_text)
        caption_text = post_text or ai_text

        # En X solo pre-rellenamos el texto del post (Parte 2). El contenido de imagen (Parte 1)
        # se mantiene exclusivamente dentro de la imagen.
        full_x_text = (post_text or ai_text).strip()
        intent_base_text, intent_tags = _extract_intent_hashtags(full_x_text, max_count=2)
        intent_text = _normalize_intent_text(intent_base_text)
        intent_url = (
            "https://twitter.com/intent/tweet?text="
            f"{quote(intent_text, safe='', encoding='utf-8')}"
        )
        if intent_tags:
            intent_url += "&hashtags=" + quote(
                ",".join(intent_tags), safe="", encoding="utf-8"
            )
        keyboard = types.InlineKeyboardMarkup()
        keyboard.row(
            types.InlineKeyboardButton(
                "🚀 Abrir en X", url=intent_url
            ),
            types.InlineKeyboardButton("📋 Copiar texto", callback_data="copy"),
            types.InlineKeyboardButton("❌ Descartar", callback_data="discard"),
        )
        try:
            if image_text:
                create_infographic(image_text=image_text, output_path=IMAGE_OUTPUT_PATH)
            else:
                generate_infographic_image(title_text, ai_text, IMAGE_OUTPUT_PATH)
            with open(IMAGE_OUTPUT_PATH, "rb") as photo:
                message = bot.send_photo(
                    chat_id, photo, caption=caption_text, reply_markup=keyboard
                )
        except Exception:
            message = bot.send_message(chat_id, caption_text, reply_markup=keyboard)
        pending_posts[message.message_id] = full_x_text
        print(f"[*] Borrador {index} enviado: {full_x_text}")


@bot.message_handler(content_types=["text"])
def handle_text_message(message):
    if message.chat.id != TELEGRAM_CHAT_ID:
        return
    text = (message.text or "").strip()
    if not text:
        return

    drafts = build_macro_drafts()
    if drafts:
        send_drafts(drafts, TELEGRAM_CHAT_ID)
    else:
        bot.send_message(TELEGRAM_CHAT_ID, "No se encontraron borradores hoy.")


@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    message_id = call.message.message_id
    draft = pending_posts.get(message_id)

    if call.data == "copy":
        if not draft:
            bot.answer_callback_query(call.id, "No se encontro el borrador.")
            return
        safe_text = _sanitize_markdown_code(draft)
        bot.send_message(
            call.message.chat.id,
            f"```\n{safe_text}\n```",
            parse_mode="Markdown",
        )
        bot.answer_callback_query(call.id, "Copia tocando el bloque.")
        try:
            bot.delete_message(call.message.chat.id, message_id)
        except Exception:
            pass
        pending_posts.pop(message_id, None)
        return

    if call.data == "discard":
        pending_posts.pop(message_id, None)
        bot.delete_message(call.message.chat.id, message_id)
        bot.answer_callback_query(call.id, "Descartado.")
        return

    bot.answer_callback_query(call.id, "Accion no valida.")


def main():
    def schedule_loop():
        last_sent = None
        while True:
            now = datetime.now()
            if 8 <= now.hour <= 21:
                current_slot = (now.date(), now.hour)
                if last_sent != current_slot:
                    drafts = build_macro_drafts()
                    if drafts:
                        send_drafts(drafts, TELEGRAM_CHAT_ID)
                    else:
                        bot.send_message(
                            TELEGRAM_CHAT_ID, "No se encontraron borradores hoy."
                        )
                    last_sent = current_slot
            time.sleep(30)

    scheduler = threading.Thread(target=schedule_loop, daemon=True)
    scheduler.start()
    bot.infinity_polling()


if __name__ == "__main__":
    main()
