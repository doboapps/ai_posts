import os
import random
import re
import textwrap
import time
from datetime import datetime
from typing import Optional

import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
from openai import OpenAI

load_dotenv()

# === Configuración y constantes ===
DEFAULT_FALLBACK_TEXT = "Actualización fútbol."
HEADLINE_FALLBACK = "Actualización fútbol"
HIGHLIGHT_COLORS = [
    (0, 255, 153),  # #00FF99 (verde neón)
    (255, 165, 0),  # #FFA500 (naranja)
    (255, 59, 48),  # #FF3B30 (rojo)
    (0, 163, 255),  # #00A3FF (azul)
]
DEFAULT_MAX_DRAFTS = 5
DEFAULT_MAX_RESULTS = 20
DEFAULT_TIME_RANGE = "week"
DEFAULT_MAX_AGE_DAYS = 3.0
DEFAULT_ALLOW_UNDATED_NEWS = True
DEFAULT_ALLOW_STALE_NEWS = False
DEFAULT_ALLOW_FALLBACK_SOURCES = True
DEFAULT_DOMAIN_BIAS_ENABLED = True
DEFAULT_DOMAIN_BIAS_RESULTS = 10

_ALLOWED_TIME_RANGES = {"day", "week", "month", "year"}
_OFFICIAL_KEYWORDS = [
    "oficial",
    "confirmado",
    "confirmada",
    "confirmación",
    "confirmacion",
    "comunicado",
    "parte médico",
    "parte medico",
    "anuncia",
    "anunciado",
    "oficializa",
    "oficialmente",
]

_IMPORTANCE_KEYWORDS = [
    "lesión",
    "lesion",
    "lesiones",
    "baja",
    "sanción",
    "sancion",
    "sancionado",
    "sancionada",
    "alineación",
    "alineacion",
    "convocatoria",
    "convocado",
    "fichaje",
    "traspaso",
    "renovación",
    "renovacion",
    "cláusula",
    "clausula",
    "marcador",
    "resultado",
    "victoria",
    "derrota",
    "empate",
]

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
]

_SECTION_SLUGS = {
    "barcelona",
    "fc-barcelona",
    "barca",
    "real-madrid",
    "realmadrid",
}

# === Búsqueda y filtrado de noticias ===
def get_hot_macro_news():
    api_key = os.getenv("TAVILY_API_KEY")
    now = datetime.now()

    if now.weekday() >= 4:
        contexto_temporal = "previas y claves de los próximos partidos del fin de semana"
    else:
        contexto_temporal = "lecturas del último partido y actualizaciones clave de la semana"

    query = (
        f"{contexto_temporal}, "
        "Real Madrid OR FC Barcelona OR Barça OR Barca, "
        "lesiones confirmadas, sanciones, alineaciones confirmadas, "
        "fichajes y mercado, tácticas, estadísticas clave, "
        "resultados y marcador en LaLiga, Champions League, Copa del Rey, "
        "comunicado oficial, parte médico, confirmado"
    )

    print(f"[*] Escaneando actualidad fútbol: {contexto_temporal}...")
    time_range = _get_time_range()
    max_results = _get_max_results()
    results = _fetch_tavily_results(
        api_key=api_key,
        query=query,
        max_results=max_results,
        time_range=time_range,
    )

    if _domain_bias_enabled():
        domain_bias_query = _build_domain_bias_query(_DOMAIN_BIAS_SOURCES)
        if domain_bias_query:
            bias_results = _fetch_tavily_results(
                api_key=api_key,
                query=f"{query} ({domain_bias_query})",
                max_results=min(_get_domain_bias_results(), max_results),
                time_range=time_range,
            )
            results = _merge_results(results, bias_results)

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
    "as.com",
    "marca.com",
    "sport.es",
    "mundodeportivo.com",
    "eldesmarque.com",
    "goal.com",
    "theathletic.com",
    "uefa.com",
    "laliga.com",
    "realmadrid.com",
    "fcbarcelona.com",
]
_ALLOWED_SOURCES = [
    *_PRIORITY_SOURCES,
    "estadiodeportivo.com",
    "okdiario.com",
    "cadenaser.com",
    "cope.es",
    "rtve.es",
    "abc.es",
    "elespanol.com",
    "elmundo.es",
    "lavanguardia.com",
    "rfef.es",
    "beinsports.com",
    "besoccer.com",
]
_BLACKLISTED_SOURCES = [
    "tiktok.com",
    "tiktokcdn.com",
]
_DOMAIN_BIAS_SOURCES = [
    "as.com",
    "marca.com",
    "sport.es",
    "mundodeportivo.com",
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


def _get_max_drafts() -> int:
    raw = (os.getenv("MAX_DRAFTS") or "").strip()
    if raw.isdigit():
        value = int(raw)
        if value > 0:
            return value
    return DEFAULT_MAX_DRAFTS


def _get_time_range() -> str:
    raw = (os.getenv("TAVILY_TIME_RANGE") or "").strip().lower()
    if raw in _ALLOWED_TIME_RANGES:
        return raw
    return DEFAULT_TIME_RANGE


def _get_max_results() -> int:
    raw = (os.getenv("TAVILY_MAX_RESULTS") or "").strip()
    if raw.isdigit():
        value = int(raw)
        if 1 <= value <= 50:
            return value
    return DEFAULT_MAX_RESULTS


def _domain_bias_enabled() -> bool:
    raw = (os.getenv("TAVILY_DOMAIN_BIAS") or "").strip()
    if raw == "":
        return DEFAULT_DOMAIN_BIAS_ENABLED
    return raw == "1"


def _get_domain_bias_results() -> int:
    raw = (os.getenv("TAVILY_DOMAIN_BIAS_RESULTS") or "").strip()
    if raw.isdigit():
        value = int(raw)
        if 1 <= value <= 50:
            return value
    return DEFAULT_DOMAIN_BIAS_RESULTS


def _build_domain_bias_query(domains: list[str]) -> str:
    if not domains:
        return ""
    terms = [f"site:{domain}" for domain in domains if domain]
    return " OR ".join(terms)


def _fetch_tavily_results(
    api_key: Optional[str],
    query: str,
    max_results: int,
    time_range: str,
) -> list[dict]:
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "advanced",
        "max_results": max_results,
        "time_range": time_range,
        "include_raw_content": True,
    }
    try:
        r = requests.post("https://api.tavily.com/search", json=payload, timeout=30)
        return r.json().get("results", [])
    except Exception as exc:
        print(f"[!] Error: {exc}")
        return []


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


def _allow_fallback_sources() -> bool:
    raw = (os.getenv("ALLOW_FALLBACK_SOURCES") or "").strip()
    if raw == "":
        return DEFAULT_ALLOW_FALLBACK_SOURCES
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


def _is_blacklisted_source(item: dict) -> bool:
    domain = _extract_domain(item.get("url", ""))
    if not domain:
        return False
    return any(_domain_matches(domain, blocked) for blocked in _BLACKLISTED_SOURCES)


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _importance_score(text: str) -> float:
    score = 0.0
    if _contains_any(text, _OFFICIAL_KEYWORDS):
        score += 1.0
    if _contains_any(text, _IMPORTANCE_KEYWORDS):
        score += 0.5
    return score


def _has_strong_signal(text: str) -> bool:
    return _contains_any(text, _OFFICIAL_KEYWORDS)


def _is_non_football_context(url: str, text: str) -> bool:
    haystack = f"{url} {text}".lower()
    return any(hint in haystack for hint in _NON_FOOTBALL_HINTS)


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


def _split_candidates_by_recency(
    candidates: list[dict],
    max_age_days: float,
) -> tuple[list[dict], list[dict], list[dict]]:
    if max_age_days <= 0:
        return candidates, [], []

    now_ts = time.time()
    max_age_seconds = max_age_days * 86400
    recent: list[dict] = []
    undated: list[dict] = []
    stale: list[dict] = []

    for candidate in candidates:
        published_ts = candidate["published_ts"]
        if not published_ts:
            undated.append(candidate)
            continue
        age_seconds = now_ts - published_ts
        if age_seconds <= max_age_seconds:
            recent.append(candidate)
        else:
            stale.append(candidate)

    return recent, undated, stale


def _rank_by_recency(
    candidates: list[dict],
    max_age_days: float,
    allow_undated: bool,
    allow_stale: bool,
) -> list[dict]:
    if not candidates:
        return []
    recent, undated, stale = _split_candidates_by_recency(candidates, max_age_days)
    ranked: list[dict] = []
    if recent:
        ranked.extend(_rank_candidates(recent))
    if allow_undated and undated:
        ranked.extend(_rank_candidates(undated))
    if allow_stale and stale:
        ranked.extend(_rank_candidates(stale))
    return ranked


def _detect_clubs(text: str) -> set[str]:
    clubs = set()
    if any(token in text for token in _REAL_TOKENS):
        clubs.add("real")
    if any(token in text for token in _BARCA_TOKENS):
        clubs.add("barca")
    return clubs


def _get_item_text(item: dict) -> str:
    title = item.get("title", "")
    content = item.get("content", "")
    return f"{title} {content}".strip().lower()


def _extract_result_score(item: dict) -> float:
    for key in ("score", "relevancy_score", "relevance_score", "relevance", "confidence"):
        value = item.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return 0.0


def _extract_published_timestamp(item: dict) -> float:
    for key in ("published_date", "published_at", "published", "date"):
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


def _build_candidate(item: dict, text: str) -> dict:
    return {
        "item": item,
        "text": text,
        "topic": _classify_topic(text),
        "clubs": _detect_clubs(text),
        "score": _extract_result_score(item),
        "importance": _importance_score(text),
        "priority": _priority_rank(item),
        "published_ts": _extract_published_timestamp(item),
    }


def _candidate_key(candidate: dict) -> str:
    item = candidate["item"]
    url = (item.get("url") or "").strip().lower()
    if url:
        return url
    title = (item.get("title") or "").strip().lower()
    if title:
        return title
    return str(id(item))


def _rank_candidates(candidates: list[dict]) -> list[dict]:
    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate["score"],
            -candidate["importance"],
            candidate["priority"],
            -candidate["published_ts"],
        ),
    )


def _select_diverse_candidates(
    candidates: list[dict],
    max_drafts: int,
    seen_topics: Optional[set[str]] = None,
) -> tuple[list[dict], set[str]]:
    final_selection: list[dict] = []
    repeats: list[dict] = []
    topics_seen = set() if seen_topics is None else seen_topics

    for candidate in candidates:
        topic = candidate["topic"]
        item = candidate["item"]
        if topic not in topics_seen:
            final_selection.append(item)
            topics_seen.add(topic)
        else:
            repeats.append(item)

        if len(final_selection) >= max_drafts:
            return final_selection, topics_seen

    if len(final_selection) < max_drafts:
        for item in repeats:
            final_selection.append(item)
            if len(final_selection) >= max_drafts:
                break

    return final_selection, topics_seen


def _mentions_target_clubs(text: str) -> bool:
    return bool(_detect_clubs(text))


def _select_club_anchors(
    ranked_candidates: list[dict],
    max_drafts: int,
) -> list[dict]:
    if max_drafts < 2:
        return []

    anchors: list[dict] = []
    used_keys: set[str] = set()
    for club in ("real", "barca"):
        if len(anchors) >= max_drafts:
            break
        for candidate in ranked_candidates:
            key = _candidate_key(candidate)
            if key in used_keys:
                continue
            if club in candidate["clubs"]:
                anchors.append(candidate)
                used_keys.add(key)
                break

    return anchors


def _classify_topic(text: str) -> str:
    lowered = text.lower()
    if any(x in lowered for x in ["clásico", "clasico", "barça vs real", "barca vs real"]):
        return "clasico"
    if any(x in lowered for x in ["fichaje", "traspaso", "mercado", "cláusula", "clausula"]):
        return "mercado"
    if any(x in lowered for x in ["lesión", "lesion", "lesiones", "injury", "sanción", "sancion", "baja"]):
        return "lesiones"
    if any(x in lowered for x in ["alineación", "alineacion", "once", "xi", "táctica", "tactica"]):
        return "tactica"
    if any(x in lowered for x in ["champions", "europa league", "liga de campeones", "ucl"]):
        return "champions"
    if any(x in lowered for x in ["laliga", "liga", "copa del rey", "supercopa"]):
        return "competicion"
    return "partido"


def select_diverse_news(news_results):
    max_drafts = _get_max_drafts()
    if not news_results:
        return []

    allow_fallback_sources = _allow_fallback_sources()
    preferred_candidates: list[dict] = []
    fallback_candidates: list[dict] = []
    for item in news_results:
        if _is_blacklisted_source(item):
            continue
        is_preferred = _is_allowed_source(item)
        if not is_preferred and not allow_fallback_sources:
            continue

        text = _get_item_text(item)
        if not _mentions_target_clubs(text):
            continue
        url = item.get("url", "")
        if _is_non_football_context(url, text):
            continue
        if _is_section_like_url(url):
            continue

        candidate = _build_candidate(item, text)
        if is_preferred:
            preferred_candidates.append(candidate)
        else:
            fallback_candidates.append(candidate)

    if not preferred_candidates and not fallback_candidates:
        return []

    max_age_days = _get_max_age_days()
    allow_undated = _allow_undated_news()
    allow_stale = _allow_stale_news()

    ranked_candidates = _rank_by_recency(
        preferred_candidates, max_age_days, allow_undated, allow_stale
    )
    if allow_fallback_sources and fallback_candidates:
        ranked_candidates.extend(
            _rank_by_recency(fallback_candidates, max_age_days, allow_undated, allow_stale)
        )

    if not ranked_candidates:
        return []

    anchors = _select_club_anchors(ranked_candidates, max_drafts)
    selected_keys = {_candidate_key(candidate) for candidate in anchors}
    final_selection = [candidate["item"] for candidate in anchors]
    seen_topics = {candidate["topic"] for candidate in anchors}

    remaining_candidates = [
        candidate
        for candidate in ranked_candidates
        if _candidate_key(candidate) not in selected_keys
    ]

    strong_candidates: list[dict] = []
    fallback_candidates: list[dict] = []
    for candidate in remaining_candidates:
        if _has_strong_signal(candidate["text"]):
            strong_candidates.append(candidate)
        else:
            fallback_candidates.append(candidate)

    if len(final_selection) < max_drafts:
        remaining = max_drafts - len(final_selection)
        extra, seen_topics = _select_diverse_candidates(
            strong_candidates, remaining, seen_topics
        )
        final_selection.extend(extra)

    if len(final_selection) < max_drafts:
        remaining = max_drafts - len(final_selection)
        extra, _ = _select_diverse_candidates(
            fallback_candidates, remaining, seen_topics
        )
        final_selection.extend(extra)

    return final_selection


# === Generación de contenido ===
def generate_expert_post(client: OpenAI, news_content: str, source_name: str):
    suggested_handle = _guess_source_handle(source_name) or source_name
    prompt = f"""
NOTICIA: {news_content}
MEDIO: {source_name}
HANDLE_SUGERIDO: {suggested_handle}

TAREA: Devuelve una respuesta dividida en 2 partes usando EXACTAMENTE el separador ###.

PARTE 1 (IMAGEN):
- Análisis futbolístico corto sobre Real Madrid o FC Barcelona + dato(s) numéricos + Probabilidad específica.
- NO incluyas hashtags ni fuentes aquí.

###

PARTE 2 (POST PARA X):
- Una PREGUNTA (una linea) que se entienda sola, incluyendo el contexto y la cifra clave (ej: "¿Le alcanza al Real Madrid con este 2-1 y 14 remates para dominar la eliminatoria?").
- 1 línea con la fuente: "Fuente: {suggested_handle}".
- 1 línea con 1 o 2 hashtags (trending).

REGLAS:
1. IDIOMA: Español de España.
2. AUTONOMÍA: El post debe entenderse sin mirar la imagen y viceverssa. Prohibido usar "esta noticia". Nombra el marcador, el rival o el torneo.
3. ENFOQUE: Solo Real Madrid o FC Barcelona.
4. ESTILO: Analista senior, directo, algo provocador, sarcasmo y algo de humor.
"""
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "Generas contenido futbolístico masculino de alto impacto para X, centrado exclusivamente en Real Madrid y FC Barcelona. Tus posts deben ser autosuficientes y generar debate inmediato."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return None

# === Utilidades de texto ===
def _normalize_spaces(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _extract_probability_line(text: str) -> str:
    for line in text.splitlines():
        if "probabilidad" in line.lower():
            return line.strip()
    match = re.search(r"(Probabilidad[^.\n]*)(?:[.\n]|$)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return "Probabilidad: N/D"


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


def _extract_macro_analysis(text: str) -> str:
    cleaned = re.sub(r"#\w+", "", text).strip()
    lines: list[str] = []

    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        line = _strip_analysis_prefix(line)
        if not line:
            continue

        lower = line.lower()
        if lower.startswith("fuente:"):
            continue

        prob_index = lower.find("probabilidad")
        if prob_index != -1:
            prefix = line[:prob_index].strip()
            if prefix:
                prefix = re.split(r"[¿?]", prefix, maxsplit=1)[0].strip()
                if prefix:
                    lines.append(prefix)
            break

        line = re.split(r"[¿?]", line, maxsplit=1)[0].strip()
        if not line:
            continue
        lines.append(line)

    paragraph = " ".join(lines).strip()
    paragraph = _strip_analysis_prefix(paragraph)
    paragraph = re.sub(r"\s{2,}", " ", paragraph)
    return paragraph or DEFAULT_FALLBACK_TEXT


def _extract_context_headline(text: str) -> str:
    paragraph = ""
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            if paragraph:
                break
            continue
        if cleaned.lower().startswith("probabilidad"):
            break
        if cleaned.lower().startswith("fuente:"):
            break
        if cleaned.startswith("#"):
            continue
        paragraph = f"{paragraph} {cleaned}".strip()

    paragraph = re.sub(r"#\w+", "", paragraph).strip()
    if not paragraph:
        return HEADLINE_FALLBACK

    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    headline = " ".join(sentences[:2]).strip()
    return headline[:170].rstrip()


# === Render de imagen ===
def _load_font(size: int) -> ImageFont.FreeTypeFont:
    font_paths = [
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/SFNSDisplay.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        "/Library/Fonts/Helvetica.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in font_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


_HIGHLIGHT_PATTERN = re.compile(r"(\S*(?:\d|[$%])\S*)")


def _draw_highlighted_line(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    line: str,
    font: ImageFont.ImageFont,
    base_fill: tuple[int, int, int],
    highlight_fill: tuple[int, int, int],
):
    cursor_x = x
    parts = re.split(_HIGHLIGHT_PATTERN, line)
    for part in parts:
        if part == "":
            continue
        is_highlight = bool(_HIGHLIGHT_PATTERN.fullmatch(part))
        fill = highlight_fill if is_highlight else base_fill
        draw.text((cursor_x, y), part, font=font, fill=fill)
        bbox = draw.textbbox((0, 0), part, font=font)
        cursor_x += bbox[2] - bbox[0]


def create_infographic(
    image_text: str,
    output_path: str,
):
    width, height = 1200, 675
    image = Image.new("RGB", (width, height), (18, 18, 18))  # #121212
    draw = ImageDraw.Draw(image)
    margin = 90
    max_width = width - 2 * margin
    max_height = height - 2 * margin

    highlight = random.choice(HIGHLIGHT_COLORS)
    base = (255, 255, 255)

    text = _normalize_spaces(image_text)
    text = _strip_analysis_prefix(text)
    if not text:
        text = DEFAULT_FALLBACK_TEXT

    font_size = 58
    min_font_size = 34
    line_spacing = 1.25

    while True:
        font = _load_font(font_size)
        sample_bbox = draw.textbbox((0, 0), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", font=font)
        avg_char_width = max(1, (sample_bbox[2] - sample_bbox[0]) // 26)
        wrap_width = max(18, int(max_width / avg_char_width))
        while True:
            lines = textwrap.wrap(
                text,
                width=wrap_width,
                break_long_words=True,
                break_on_hyphens=False,
            )
            widest = 0
            for line in lines:
                bbox = draw.textbbox((0, 0), line, font=font)
                widest = max(widest, bbox[2] - bbox[0])
            if widest <= max_width or wrap_width <= 18:
                break
            wrap_width = max(18, wrap_width - 2)
        bbox = font.getbbox("Ag")
        line_height = bbox[3] - bbox[1]
        total_height = int(len(lines) * line_height * line_spacing)

        if total_height <= max_height or font_size <= min_font_size:
            break
        font_size -= 2

    if not lines:
        lines = [text]

    while True:
        font = _load_font(font_size)
        bbox = font.getbbox("Ag")
        line_height = bbox[3] - bbox[1]
        total_height = int(len(lines) * line_height * line_spacing)
        if total_height <= max_height or font_size <= min_font_size:
            break
        font_size -= 2

    bbox = font.getbbox("Ag")
    line_height = bbox[3] - bbox[1]
    total_height = int(len(lines) * line_height * line_spacing)
    if total_height > max_height:
        max_lines = max(1, max_height // max(1, int(line_height * line_spacing)))
        lines = lines[:max_lines]
        if lines:
            last = lines[-1].rstrip()
            if last and not last.endswith("…"):
                lines[-1] = (last[:-1].rstrip() + "…") if len(last) > 1 else "…"
        total_height = int(len(lines) * line_height * line_spacing)
    start_y = (height - total_height) // 2

    for i, line in enumerate(lines):
        parts = re.split(_HIGHLIGHT_PATTERN, line)
        widths = []
        for part in parts:
            if part == "":
                continue
            bbox = draw.textbbox((0, 0), part, font=font)
            widths.append(bbox[2] - bbox[0])
        line_width = sum(widths)
        start_x = (width - line_width) // 2

        y = start_y + int(i * line_height * line_spacing)
        _draw_highlighted_line(
            draw,
            start_x,
            y,
            line,
            font=font,
            base_fill=base,
            highlight_fill=highlight,
        )

    image.save(output_path, format="PNG")


def generate_infographic_image(
    title: str,
    post_text: str,
    output_path: str,
):
    macro_analysis = _extract_macro_analysis(post_text)
    probability = _extract_probability_line(post_text)
    combined = _normalize_spaces(macro_analysis)
    prob = _normalize_spaces(probability)
    if prob:
        combined = f"{combined} {prob}".strip()
    create_infographic(
        image_text=combined,
        output_path=output_path,
    )


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
        source_name = _extract_domain(url)
        post = generate_expert_post(client, item.get("content", ""), source_name)
        if post:
            post = _strip_analysis_prefix(post)
            drafts.append(
                {
                    "ai_text": post,
                    "title": _extract_context_headline(post),
                    "url": url,
                }
            )

        time.sleep(2)

    return drafts
