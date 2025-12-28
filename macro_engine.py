import calendar
import html
import os
import re
import time
from datetime import datetime
from typing import Optional

import feedparser
import requests
from dotenv import load_dotenv
from openai import OpenAI
try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover (py<3.9)
    ZoneInfo = None  # type: ignore[misc,assignment]

load_dotenv()

# === Configuración y constantes ===
DEFAULT_MAX_DRAFTS = 5
DEFAULT_RSS_TIMEOUT = 20
DEFAULT_RSS_MAX_ITEMS_PER_FEED = 25
DEFAULT_RSS_CONTENT_LIMIT = 1200
DEFAULT_MAX_AGE_DAYS = 3.0
DEFAULT_ALLOW_UNDATED_NEWS = True
DEFAULT_ALLOW_STALE_NEWS = False
DEFAULT_ONLY_TODAY = False

_NON_FOOTBALL_HINTS = [
    "baloncesto",
    "basket",
    "nba",
    "acb",
    "euroliga",
    "euroleague",
    "liga endesa",
    "liga-endesa",
    "endesa",
    'femenino',
]

_BLOCKED_URL_CONTAINS = [
    "mercado-de-fichajes-en-directo",
]

_SECTION_SLUGS = {
    "barcelona",
    "fc-barcelona",
    "barca",
    "real-madrid",
    "realmadrid",
}

_STOPWORDS = {
    "para",
    "sobre",
    "desde",
    "hasta",
    "entre",
    "tras",
    "ante",
    "contra",
    "cuando",
    "donde",
    "como",
    "porque",
    "pero",
    "aunque",
    "esta",
    "este",
    "estos",
    "estas",
    "del",
    "las",
    "los",
    "una",
    "uno",
    "unos",
    "unas",
    "con",
    "sin",
    "por",
    "que",
    "sus",
    "su",
    "al",
}

_BAD_QUESTION_STARTS = (
    "¿por qué",
    "¿de verdad",
    "¿tan ",
    "¿hasta cuándo",
    "¿si ",
)

_RSS_SOURCES = [
    {"name": "Marca", "url": "https://e00-xlk-ue-marca.uecdn.es/rss/futbol.xml"},
    {"name": "Sport", "url": "https://www.sport.es/es/rss/barca/rss.xml"},
    {"name": "AS", "url": "https://feeds.as.com/mrss-s/pages/as/site/as.com/section/futbol/portada/"},
    {"name": "El Periodico", "url": "https://www.elperiodico.com/es/rss/barca/rss.xml"},
    {"name": "El Pais", "url": "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/deportes/portada"},
    {"name": "Mundo Deportivo", "url": "https://www.mundodeportivo.com/feed/rss/portada"},
]

# === Búsqueda y filtrado de noticias ===
def get_hot_macro_news():
    print("[*] Escaneando RSS de futbol...")
    results: list[dict] = []
    for source in _RSS_SOURCES:
        source_results = _fetch_rss_source(source)
        results = _merge_results(results, source_results)
    return results

_REAL_TOKENS = [
    "real madrid",
    "realmadrid",
    "real-madrid",
    "bernabeu",
    "bernabéu",
    "santiago bernabéu",
    "los blancos",
    "merengue",
]

_BARCA_TOKENS = [
    "fc barcelona",
    "barcelona",
    "barça",
    "barca",
    "fcb",
    "blaugrana",
    "culé",
    "camp nou",
    "nou camp",
]

_PRIORITY_SOURCES = [
    "marca.com",
    "sport.es",
    "as.com",
    "elperiodico.com",
    "elpais.com",
    "mundodeportivo.com",
]
_ALLOWED_SOURCES = list(_PRIORITY_SOURCES)
_AGGREGATOR_DOMAINS = [
    "news.google.com",
    "feedproxy.google.com",
    "feedburner.com",
]


def _extract_domain(url: str) -> str:
    if not url:
        return ""
    domain = url.split("//")[-1].split("/")[0].lower()
    return domain.replace("www.", "")


def _domain_matches(domain: str, priority: str) -> bool:
    if not domain or not priority:
        return False
    return domain == priority or domain.endswith(f".{priority}")


def _pick_entry_url(entry: dict) -> str:
    candidates: list[str] = []
    primary = (entry.get("link") or "").strip()
    if primary:
        candidates.append(primary)
    for link_info in entry.get("links") or []:
        href = (link_info.get("href") or "").strip()
        if href:
            candidates.append(href)

    for url in candidates:
        domain = _extract_domain(url)
        if not domain:
            continue
        if any(_domain_matches(domain, agg) for agg in _AGGREGATOR_DOMAINS):
            continue
        return url

    return candidates[0] if candidates else ""


def _get_max_drafts() -> int:
    raw = (os.getenv("MAX_DRAFTS") or "").strip()
    if raw.isdigit():
        value = int(raw)
        if value > 0:
            return value
    return DEFAULT_MAX_DRAFTS


def _get_rss_timeout() -> int:
    raw = (os.getenv("RSS_TIMEOUT_SECS") or "").strip()
    if raw.isdigit():
        value = int(raw)
        if value > 0:
            return value
    return DEFAULT_RSS_TIMEOUT


def _get_rss_max_items_per_feed() -> int:
    raw = (os.getenv("RSS_MAX_ITEMS_PER_FEED") or "").strip()
    if raw.isdigit():
        value = int(raw)
        if value > 0:
            return value
    return DEFAULT_RSS_MAX_ITEMS_PER_FEED


def _get_rss_content_limit() -> int:
    raw = (os.getenv("RSS_CONTENT_LIMIT") or "").strip()
    if raw.isdigit():
        value = int(raw)
        if value > 0:
            return value
    return DEFAULT_RSS_CONTENT_LIMIT


def _strip_html(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(cleaned)


def _compact_spaces(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _extract_entry_text(entry: dict) -> str:
    summary = (entry.get("summary") or entry.get("description") or "").strip()
    summary_detail = ""
    detail = entry.get("summary_detail") or {}
    if isinstance(detail, dict):
        summary_detail = (detail.get("value") or "").strip()
    content_value = ""
    contents = entry.get("content") or []
    if contents:
        content_value = (contents[0].get("value") or "").strip()
    raw_text = " ".join(
        part for part in [summary, summary_detail, content_value] if part
    ).strip()
    cleaned = _compact_spaces(_strip_html(raw_text))
    limit = _get_rss_content_limit()
    if limit > 0 and len(cleaned) > limit:
        trimmed = cleaned[:limit].rsplit(" ", 1)[0].strip()
        return trimmed or cleaned[:limit]
    return cleaned


def _extract_entry_timestamp(entry: dict) -> float:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            try:
                return float(calendar.timegm(value))
            except Exception:
                continue
    return 0.0


def _entry_to_item(entry: dict, source_name: str) -> Optional[dict]:
    title = (entry.get("title") or "").strip()
    url = _pick_entry_url(entry)
    if not title or not url:
        return None
    content = _extract_entry_text(entry)
    return {
        "title": title,
        "content": content,
        "url": url,
        "source": source_name,
        "published_ts": _extract_entry_timestamp(entry),
    }


def _fetch_rss_source(source: dict) -> list[dict]:
    url = (source.get("url") or "").strip()
    name = source.get("name") or "RSS"
    if not url:
        return []
    try:
        response = requests.get(
            url,
            timeout=_get_rss_timeout(),
            headers={"User-Agent": "Mozilla/5.0 (compatible; ai_posts/1.0)"},
        )
        response.raise_for_status()
    except Exception as exc:
        print(f"[!] RSS error ({name}): {exc}")
        return []

    feed = feedparser.parse(response.content)
    entries = feed.entries or []
    max_items = _get_rss_max_items_per_feed()
    if max_items > 0:
        entries = entries[:max_items]

    results: list[dict] = []
    for entry in entries:
        item = _entry_to_item(entry, name)
        if item:
            results.append(item)
    return results


def _merge_results(primary: list[dict], secondary: list[dict]) -> list[dict]:
    if not secondary:
        return primary
    merged: list[dict] = []
    seen: set[str] = set()
    for item in primary + secondary:
        url = (item.get("url") or "").strip().lower()
        key = url or (item.get("title") or "").strip().lower()
        if key:
            if key in seen:
                continue
            seen.add(key)
        merged.append(item)
    return merged


def _get_max_age_days() -> float:
    raw = (os.getenv("MAX_NEWS_AGE_DAYS") or "").strip()
    if not raw:
        return DEFAULT_MAX_AGE_DAYS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_MAX_AGE_DAYS
    return value if value > 0 else DEFAULT_MAX_AGE_DAYS


def _only_today() -> bool:
    raw = (os.getenv("ONLY_TODAY") or "").strip()
    if raw == "":
        return DEFAULT_ONLY_TODAY
    return raw == "1"


def _get_news_timezone() -> Optional[datetime.tzinfo]:
    tz_name = (os.getenv("NEWS_TZ") or os.getenv("RUN_TZ") or "UTC").strip() or "UTC"
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def _is_today(published_ts: float) -> bool:
    if not published_ts:
        return False
    tzinfo = _get_news_timezone()
    if tzinfo:
        return datetime.fromtimestamp(published_ts, tz=tzinfo).date() == datetime.now(tzinfo).date()
    return datetime.utcfromtimestamp(published_ts).date() == datetime.utcnow().date()


def _allow_undated_news() -> bool:
    raw = (os.getenv("ALLOW_UNDATED_NEWS") or "").strip()
    if raw == "":
        return DEFAULT_ALLOW_UNDATED_NEWS
    return raw == "1"


def _allow_stale_news() -> bool:
    raw = (os.getenv("ALLOW_STALE_NEWS") or "").strip()
    if raw == "":
        return DEFAULT_ALLOW_STALE_NEWS
    return raw == "1"


def _priority_rank(item: dict) -> int:
    domain = _extract_domain(item.get("url", ""))
    for idx, priority in enumerate(_PRIORITY_SOURCES):
        if _domain_matches(domain, priority):
            return idx
    return len(_PRIORITY_SOURCES) + 1


def _is_allowed_source(item: dict) -> bool:
    domain = _extract_domain(item.get("url", ""))
    if not domain:
        return False
    return any(_domain_matches(domain, allowed) for allowed in _ALLOWED_SOURCES)


def _is_non_football_context(url: str, text: str) -> bool:
    haystack = f"{url} {text}".lower()
    return any(hint in haystack for hint in _NON_FOOTBALL_HINTS)


def _is_blocked_url(url: str) -> bool:
    cleaned = (url or "").lower()
    if not cleaned:
        return False
    return any(token in cleaned for token in _BLOCKED_URL_CONTAINS)


def _is_section_like_url(url: str) -> bool:
    if not url:
        return True
    cleaned = url.split("?", 1)[0].split("#", 1)[0]
    path_part = cleaned.split("//")[-1]
    parts = path_part.split("/", 1)
    if len(parts) < 2:
        return True
    path = parts[1].strip("/")
    if not path:
        return True
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) <= 2 and not any(char.isdigit() for char in path):
        return True
    last = segments[-1].lower()
    if last.endswith(".html"):
        slug = last[:-5]
        if slug in _SECTION_SLUGS:
            return True
        if not any(char.isdigit() for char in slug) and len(segments) <= 2:
            return True
    return False


def _extract_published_timestamp(item: dict) -> float:
    for key in ("published_ts", "published_date", "published_at", "published", "date"):
        value = item.get(key)
        if not value:
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                cleaned = value.replace("Z", "+00:00")
                return datetime.fromisoformat(cleaned).timestamp()
            except ValueError:
                continue
    return 0.0


def _detect_clubs(text: str) -> set[str]:
    clubs = set()
    if any(token in text for token in _REAL_TOKENS):
        clubs.add("real")
    if any(token in text for token in _BARCA_TOKENS):
        clubs.add("barca")
    return clubs


def _extract_keywords(text: str, limit: int = 8) -> list[str]:
    tokens = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]{4,}", text or "")
    keywords: list[str] = []
    seen = set()
    for token in tokens:
        normalized = token.lower()
        if normalized in _STOPWORDS:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        keywords.append(token)
        if len(keywords) >= limit:
            break
    return keywords


def _extract_question_line(text: str) -> str:
    if not text:
        return ""
    parts = text.split("###", 1)
    body = parts[1] if len(parts) > 1 else parts[0]
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lower = line.lower()
        if lower.startswith("fuente:"):
            continue
        if line.startswith("#"):
            continue
        return line
    return ""


def _question_needs_regen(question: str, title: str, content: str) -> bool:
    if not question:
        return True
    lower = question.strip().lower()
    if len(lower) < 35:
        return True
    if any(lower.startswith(start) for start in _BAD_QUESTION_STARTS):
        return True
    keywords = _extract_keywords(title)
    if not keywords:
        keywords = _extract_keywords(content)
    if keywords and not any(keyword.lower() in lower for keyword in keywords):
        return True
    return False


def _club_label_from_set(clubs: set[str]) -> Optional[str]:
    if clubs == {"real"}:
        return "real"
    if clubs == {"barca"}:
        return "barca"
    return None


def select_diverse_news(news_results):
    max_drafts = _get_max_drafts()
    max_drafts -= max_drafts % 2
    if max_drafts < 2 or not news_results:
        return []

    only_today = _only_today()
    allow_undated = _allow_undated_news()
    allow_stale = _allow_stale_news()
    cutoff_ts = None
    if not only_today:
        max_age_days = _get_max_age_days()
        if max_age_days > 0:
            cutoff_ts = time.time() - (max_age_days * 86400)

    candidates: list[dict] = []
    for item in news_results:
        if not _is_allowed_source(item):
            continue

        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        content = (item.get("content") or "").strip()
        title_url_text = f"{title} {url}".strip().lower()
        detection_text = f"{title} {url} {content}".strip().lower()
        clubs = _detect_clubs(detection_text)
        if not clubs:
            continue
        if _is_blocked_url(url):
            continue
        if _is_non_football_context(url, title_url_text):
            continue
        if _is_section_like_url(url):
            continue

        published_ts = _extract_published_timestamp(item)
        if not published_ts:
            if only_today or not allow_undated:
                continue
        if published_ts and only_today and not _is_today(published_ts):
            continue
        if not only_today and published_ts and cutoff_ts is not None and published_ts < cutoff_ts:
            if not allow_stale:
                continue

        key = (url or title).strip().lower()
        candidates.append(
            {
                "item": item,
                "priority": _priority_rank(item),
                "published_ts": published_ts or 0.0,
                "clubs": clubs,
                "key": key,
            }
        )

    if not candidates:
        return []

    candidates.sort(
        key=lambda candidate: (
            -candidate["published_ts"],
            candidate["priority"],
        )
    )

    target_per_club = max_drafts // 2
    real_count = 0
    barca_count = 0
    selected: list[dict] = []
    selected_keys: set[str] = set()

    for candidate in candidates:
        if len(selected) >= max_drafts:
            break
        clubs = candidate["clubs"]
        if not clubs:
            continue

        assigned_club: Optional[str] = None
        if clubs == {"real"}:
            if real_count < target_per_club:
                assigned_club = "real"
        elif clubs == {"barca"}:
            if barca_count < target_per_club:
                assigned_club = "barca"
        else:
            if real_count < target_per_club or barca_count < target_per_club:
                if real_count < target_per_club and barca_count < target_per_club:
                    if (target_per_club - real_count) >= (target_per_club - barca_count):
                        assigned_club = "real"
                    else:
                        assigned_club = "barca"
                elif real_count < target_per_club:
                    assigned_club = "real"
                else:
                    assigned_club = "barca"

        if assigned_club:
            key = candidate["key"]
            if key in selected_keys:
                continue
            candidate["item"]["club"] = assigned_club
            selected.append(candidate)
            selected_keys.add(key)
            if assigned_club == "real":
                real_count += 1
            else:
                barca_count += 1

        if real_count >= target_per_club and barca_count >= target_per_club:
            break

    balanced_target = min(real_count, barca_count)
    if balanced_target == 0:
        return []

    balanced_selected: list[dict] = []
    real_count = 0
    barca_count = 0
    for candidate in selected:
        club = candidate["item"].get("club")
        if club == "real":
            if real_count >= balanced_target:
                continue
            real_count += 1
        elif club == "barca":
            if barca_count >= balanced_target:
                continue
            barca_count += 1
        else:
            continue
        balanced_selected.append(candidate)
        if real_count >= balanced_target and barca_count >= balanced_target:
            break

    return [candidate["item"] for candidate in balanced_selected]


# === Generación de contenido ===
def generate_expert_post(
    client: OpenAI,
    news_title: str,
    news_content: str,
    source_name: str,
):
    suggested_handle = _guess_source_handle(source_name) or source_name
    title = _normalize_spaces(news_title)
    content = _normalize_spaces(news_content)
    if content and title:
        noticia = f"{title}. {content}"
    else:
        noticia = title or content

    # MODIFICACIÓN: Prompt diseñado para preguntas incisivas y concretas.
    base_prompt = f"""
NOTICIA: {noticia}
MEDIO: {source_name}
HANDLE_SUGERIDO: {suggested_handle}

TAREA: Devuelve una respuesta dividida en 2 partes usando EXACTAMENTE el separador ###.

PARTE 1 (RESUMEN):
- Resumen de la noticia, máximo 170 caracteres.
- Texto limpio, sin etiquetas tipo "Resumen:".

###

PARTE 2 (PREGUNTA + FUENTE + HASHTAGS):
- Genera una PREGUNTA CORTA (máximo 85 caracteres), 1 línea.
- Tono: incisivo y directo, sin humor forzado pero con algo sarcasmo.
- Debe ser concreta y polémica: plantea una contradicción o doble rasero del hecho.
- Incluye al menos 1 elemento literal de NOTICIA (nombre propio, club o competición).
- NO preguntes datos obvios (ej: "¿Quién ganó?"). Cuestiona el "cómo" o el "por qué".
- EVITA muletillas genéricas como: "¿Hasta cuándo?", "¿Tan difícil?", "¿De verdad?".
- NO empieces con "¿Por qué", "¿De verdad", "¿Tan", "¿Hasta cuándo".
- NO repitas el resumen.
- 1 línea con la fuente: "Fuente: {suggested_handle}".
- 1 línea final con 1 hashtag que sea el más posible trending topic relacionado con el tema.

REGLAS GENERALES:
1. IDIOMA: Español de España (coloquial futbolero).
2. SIN ICONOS NI EMOJIS.
3. ENFOQUE: Solo Real Madrid o FC Barcelona.
4. RIGOR: Usa solo información explícita de NOTICIA. No inventes contexto (tabla, entrenador, resultados, fichajes, lesiones o premios).
5. NOMBRES: No menciones personas o equipos que no aparezcan en NOTICIA.
6. SI HAY POCA INFORMACIÓN: Haz una pregunta general sin afirmar hechos externos.
"""
    keywords = _extract_keywords(title) or _extract_keywords(content)
    keyword_hint = ", ".join(keywords[:5])
    retry_note = ""
    last_response = None

    for _ in range(2):
        prompt = base_prompt + retry_note
        try:
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": "Eres un analista de fútbol incisivo y viral en X, pero riguroso: no inventas datos ni contexto, y evitas muletillas o frases vacías."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
            )
        except Exception:
            break

        content_text = resp.choices[0].message.content.strip()
        if not content_text:
            continue
        last_response = content_text

        question = _extract_question_line(content_text)
        if not _question_needs_regen(question, title, content):
            return content_text

        if keyword_hint:
            retry_note = (
                "\n\nREINTENTO: La pregunta fue genérica. Devuelve de nuevo las 2 partes. "
                "La pregunta debe incluir al menos uno de estos términos: "
                f"{keyword_hint}."
            )
        else:
            retry_note = (
                "\n\nREINTENTO: La pregunta fue genérica. Devuelve de nuevo las 2 partes."
            )

    return last_response

# === Utilidades de texto ===
def _normalize_spaces(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _strip_analysis_prefix(text: str) -> str:
    return re.sub(r"(?im)^\s*an[aá]lisis\s*:?\s*", "", text).strip()


def _guess_source_handle(source_name: str) -> Optional[str]:
    raw = (source_name or "").strip()
    if not raw:
        return None
    if raw.startswith("@"):
        handle = raw
    else:
        domain = raw.split("/")[-1].split(":")[0]
        domain = domain.replace("www.", "")
        base = domain.split(".")[0]
        base = re.sub(r"[^A-Za-z0-9_]", "", base)
        if not base:
            return None
        handle = f"@{base}"
    return handle[:30]


# === Orquestación ===
def build_macro_drafts():
    """Orquesta el flujo fútbol y devuelve borradores listos para revision."""
    client = OpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com"
    )

    raw_news = get_hot_macro_news()
    if not raw_news:
        return []

    diverse_news = select_diverse_news(raw_news)
    print(f"[*] Analizando {len(diverse_news)} eventos clave.")

    drafts = []
    for item in diverse_news:
        url = item.get("url", "")
        club = (item.get("club") or "").strip()
        source_name = _extract_domain(url)
        post = generate_expert_post(
            client,
            item.get("title", ""),
            item.get("content", ""),
            source_name,
        )
        if post:
            post = _strip_analysis_prefix(post)
            drafts.append(
                {
                    "ai_text": post,
                    "url": url,
                    "club": club,
                }
            )

        time.sleep(2)

    return drafts
