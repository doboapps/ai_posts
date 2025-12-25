import os
import tempfile
from datetime import datetime
from typing import Optional, Union
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


def _normalize_intent_text(text: str) -> str:
    normalized = " ".join((text or "").split())
    return normalized.replace("%", "%25")


def _extract_intent_hashtags(text: str, max_count: int = 2) -> tuple[str, list[str]]:
    import re

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
    import re

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


def _should_run_now() -> bool:
    try:
        from zoneinfo import ZoneInfo
    except ImportError:  # pragma: no cover (py<3.9)
        ZoneInfo = None  # type: ignore[misc,assignment]

    tz_name = (os.getenv("RUN_TZ") or "UTC").strip() or "UTC"
    start_hour = int(os.getenv("RUN_START_HOUR") or "8")
    end_hour = int(os.getenv("RUN_END_HOUR") or "21")

    if ZoneInfo:
        try:
            now = datetime.now(ZoneInfo(tz_name))
        except Exception:
            now = datetime.now(ZoneInfo("UTC"))
    else:
        now = datetime.utcnow()
    hour = now.hour
    return start_hour <= hour <= end_hour


def _require_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Falta la variable de entorno requerida: {name}")
    return value


def send_drafts_scheduled(drafts: list[dict], token: str, chat_id: Union[int, str]) -> int:
    bot = telebot.TeleBot(token)
    sent = 0

    with tempfile.TemporaryDirectory() as temp_dir:
        image_path = os.path.join(temp_dir, "infographic.png")

        for draft in drafts:
            ai_text = (draft.get("ai_text") or draft.get("tweet_text") or draft.get("draft") or "").strip()
            title_text = (draft.get("title") or "").strip()
            if not ai_text:
                continue

            image_text, post_text = _split_ai_response(ai_text)
            caption_text = (post_text or ai_text).strip()

            intent_base_text, intent_tags = _extract_intent_hashtags(caption_text, max_count=2)
            intent_text = _normalize_intent_text(intent_base_text)
            intent_url = (
                "https://twitter.com/intent/tweet?text="
                f"{quote(intent_text, safe='', encoding='utf-8')}"
            )
            if intent_tags:
                intent_url += "&hashtags=" + quote(",".join(intent_tags), safe="", encoding="utf-8")

            keyboard = types.InlineKeyboardMarkup()
            keyboard.row(types.InlineKeyboardButton("🚀 Abrir en X", url=intent_url))

            try:
                if image_text:
                    create_infographic(image_text=image_text, output_path=image_path)
                else:
                    generate_infographic_image(title_text, ai_text, image_path)
                with open(image_path, "rb") as photo:
                    bot.send_photo(chat_id, photo, caption=caption_text, reply_markup=keyboard)
            except Exception:
                bot.send_message(chat_id, caption_text, reply_markup=keyboard)

            sent += 1

    return sent


def main() -> int:
    if not _should_run_now():
        tz_name = (os.getenv("RUN_TZ") or "UTC").strip() or "UTC"
        print(f"[*] Fuera de ventana horaria; no se ejecuta (RUN_TZ={tz_name}).")
        return 0

    try:
        _require_env("TAVILY_API_KEY")
        _require_env("DEEPSEEK_API_KEY")
        token = _require_env("TELEGRAM_TOKEN")
        chat_id = _get_chat_id(_require_env("TELEGRAM_CHAT_ID"))
        if chat_id is None:
            raise RuntimeError("TELEGRAM_CHAT_ID inválido")
    except Exception as exc:
        print(f"[!] Configuración incompleta: {exc}")
        return 2

    drafts = build_macro_drafts()
    if not drafts:
        if (os.getenv("SEND_EMPTY_MESSAGE") or "").strip() == "1":
            bot = telebot.TeleBot(token)
            bot.send_message(chat_id, "No se encontraron borradores en esta ejecución.")
        print("[*] Sin borradores.")
        return 0

    max_drafts_raw = (os.getenv("MAX_DRAFTS") or "").strip()
    if max_drafts_raw.isdigit():
        drafts = drafts[: int(max_drafts_raw)]

    sent = send_drafts_scheduled(drafts=drafts, token=token, chat_id=chat_id)
    print(f"[*] Enviados {sent} borradores a Telegram.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
