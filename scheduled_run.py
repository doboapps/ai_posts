import os
from datetime import datetime
from typing import Optional, Union
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

    for index, draft in enumerate(drafts, start=1):
        ai_text = (draft.get("ai_text") or draft.get("tweet_text") or draft.get("draft") or "").strip()
        source_url = (draft.get("url") or "").strip()

        if not ai_text:
            continue

        summary_text, post_text = _split_ai_response(ai_text)
        club_prefix = _club_prefix(draft.get("club", ""))
        combined_text = _build_post_text(summary_text, post_text, club_prefix)
        separator = "=" * 64
        print(f"\n{separator}")
        print(f"[debug] Borrador {index}")
        print(f"[debug] Resumen (Parte 1): {summary_text}")
        print(f"[debug] Pregunta (Parte 2): {post_text}")
        print(f"[debug] Enlace noticia: {source_url or 'URL no disponible'}")
        caption_text = _append_source_link(combined_text or ai_text, source_url)
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
            intent_url += "&hashtags=" + quote(",".join(intent_tags), safe="", encoding="utf-8")

        keyboard = types.InlineKeyboardMarkup()
        keyboard.row(types.InlineKeyboardButton("🚀 Abrir en X", url=intent_url))

        bot.send_message(chat_id, caption_text, reply_markup=keyboard)

        sent += 1

    return sent


def main() -> int:
    if not _should_run_now():
        tz_name = (os.getenv("RUN_TZ") or "UTC").strip() or "UTC"
        print(f"[*] Fuera de ventana horaria; no se ejecuta (RUN_TZ={tz_name}).")
        return 0

    try:
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
