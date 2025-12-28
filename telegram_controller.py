import re
import threading
import time
from datetime import datetime
from typing import Dict, Optional, Union
from urllib.parse import quote

import telebot
from dotenv import load_dotenv
from telebot import types

from macro_engine import build_macro_drafts

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


def _append_source_link(text: str, url: str) -> str:
    cleaned_url = (url or "").strip()
    base_text = (text or "").strip()
    if not cleaned_url:
        return base_text
    if cleaned_url in base_text:
        return base_text
    if not base_text:
        return cleaned_url
    lines = base_text.splitlines()
    for idx, line in enumerate(lines):
        if line.strip().lower().startswith("fuente:"):
            lines.insert(idx + 1, cleaned_url)
            return "\n".join(lines).strip()
    return f"{base_text}\n{cleaned_url}"


def _club_prefix(club: str) -> str:
    normalized = (club or "").strip().lower()
    if normalized == "real":
        return "🔵⚪"
    if normalized == "barca":
        return "🔴🔵"
    return ""


def _build_post_text(summary_text: str, post_text: str, prefix: str = "") -> str:
    summary = (summary_text or "").strip()
    post = (post_text or "").strip()
    if not post:
        return summary
    if not summary:
        return post

    lines = [line.strip() for line in post.splitlines() if line.strip()]
    source_lines = [line for line in lines if line.lower().startswith("fuente:")]
    hashtag_lines = [line for line in lines if line.startswith("#")]
    question_lines = [
        line for line in lines if line not in source_lines and line not in hashtag_lines
    ]

    question_text = " ".join(question_lines).strip() if question_lines else post
    tail_lines: list[str] = []
    tail_lines.extend(source_lines)
    tail_lines.extend(hashtag_lines)

    segments: list[str] = []
    if question_text:
        segments.append(question_text)
    if summary:
        segments.append(summary)
    if tail_lines:
        segments.append("\n".join(tail_lines))

    if prefix and segments:
        segments[0] = f"{prefix} {segments[0]}".strip()
    elif prefix:
        segments = [prefix]

    return "\n\n".join(segment for segment in segments if segment).strip()


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

def send_drafts(drafts, chat_id):
    for index, draft in enumerate(drafts, start=1):
        if isinstance(draft, dict):
            ai_text = (draft.get("ai_text") or draft.get("tweet_text") or draft.get("draft") or "").strip()
            source_url = (draft.get("url") or "").strip()
            club_prefix = _club_prefix(draft.get("club", ""))
        else:
            ai_text = (draft or "").strip()
            source_url = ""
            club_prefix = ""

        if not ai_text:
            continue

        summary_text, post_text = _split_ai_response(ai_text)
        combined_text = _build_post_text(summary_text, post_text, club_prefix)
        caption_text = _append_source_link(combined_text or ai_text, source_url)

        # En X pre-rellenamos el texto completo del post (resumen + pregunta).
        full_x_text = caption_text
        intent_source_text = combined_text or ai_text
        intent_base_text, intent_tags = _extract_intent_hashtags(intent_source_text, max_count=2)
        intent_text = _normalize_intent_text(intent_base_text)
        intent_url = (
            "https://twitter.com/intent/tweet?text="
            f"{quote(intent_text, safe='', encoding='utf-8')}"
        )
        if source_url:
            intent_url += "&url=" + quote(source_url, safe="", encoding="utf-8")
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
