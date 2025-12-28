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
        "lesiones confirmadas, sanciones, alineaciones probables, "
        "fichajes y mercado, tácticas, estadísticas clave, "
        "resultados y marcador en LaLiga, Champions League, Copa del Rey"
    )

    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "advanced",
        "max_results": 12,
        "time_range": "day",
        "include_raw_content": True
    }

    try:
        print(f"[*] Escaneando actualidad fútbol: {contexto_temporal}...")
        r = requests.post("https://api.tavily.com/search", json=payload, timeout=30)
        return r.json().get("results", [])
    except Exception as e:
        print(f"[!] Error: {e}")
        return []

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

_CLUB_TOKENS = _REAL_TOKENS + _BARCA_TOKENS

_PRIORITY_SOURCES = [
    "as.com",
    "marca.com",
    "sport.es",
    "mundodeportivo.com",
    "eldesmarque.com",
    "okdiario.com/deportes",
    "goal.com/es",
    "theathletic.com/spain",
]
_ALLOWED_SOURCES = list(_PRIORITY_SOURCES)


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


def _prioritize_news_results(news_results: list[dict]) -> list[dict]:
    buckets: dict[int, list[dict]] = {}
    for item in news_results:
        rank = _priority_rank(item)
        buckets.setdefault(rank, []).append(item)
    ordered: list[dict] = []
    for rank in sorted(buckets):
        bucket = buckets[rank]
        random.shuffle(bucket)
        ordered.extend(bucket)
    return ordered


def _mentions_target_clubs(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in _CLUB_TOKENS)


def _classify_topic(text: str) -> str:
    if any(x in text for x in ["clásico", "clasico", "barça vs real", "barca vs real"]):
        return "clasico"
    if any(x in text for x in ["fichaje", "traspaso", "mercado", "cláusula", "clausula"]):
        return "mercado"
    if any(x in text for x in ["lesión", "lesion", "lesiones", "injury", "sanción", "sancion", "baja"]):
        return "lesiones"
    if any(x in text for x in ["alineación", "alineacion", "once", "xi", "táctica", "tactica"]):
        return "tactica"
    if any(x in text for x in ["champions", "europa league", "liga de campeones", "ucl"]):
        return "champions"
    if any(x in text for x in ["laliga", "liga", "copa del rey", "supercopa"]):
        return "competicion"
    return "partido"


def select_diverse_news(news_results):
    final_selection = []
    temas_vistos = set()
    repeated_candidates = []
    max_drafts = _get_max_drafts()

    # Priorizamos fuentes clave y mezclamos dentro de cada grupo
    news_results = _prioritize_news_results(news_results)

    for item in news_results:
        if not _is_allowed_source(item):
            continue

        title = item.get("title", "")
        content = item.get("content", "")
        text = f"{title} {content}".lower()

        # 1. Solo queremos noticias de Real Madrid o FC Barcelona
        if not _mentions_target_clubs(text):
            continue

        # 2. Clasificación para diversidad de temas futbolísticos
        tema = _classify_topic(text)

        # 3. Priorizamos temas distintos, pero guardamos repetidos como fallback
        if tema not in temas_vistos:
            final_selection.append(item)
            temas_vistos.add(tema)
        else:
            repeated_candidates.append(item)

        # Límite configurable de noticias para no saturar
        if len(final_selection) >= max_drafts:
            break

    if len(final_selection) < max_drafts:
        for item in repeated_candidates:
            final_selection.append(item)
            if len(final_selection) >= max_drafts:
                break

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
