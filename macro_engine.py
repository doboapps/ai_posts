import os
import random
import re
import textwrap
import time
from datetime import datetime, timedelta
from typing import Optional

import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
from openai import OpenAI

load_dotenv()

def get_hot_macro_news():
    api_key = os.getenv("TAVILY_API_KEY")
    now = datetime.now()

    # Lógica de ventana: Si estamos a final de mes/trimestre, miramos al frente
    # Si quedan menos de 10 días para el mes siguiente, pivotamos la búsqueda
    if (now + timedelta(days=10)).month != now.month:
        proximo_mes = (now + timedelta(days=10)).strftime("%B")
        anio_objetivo = (now + timedelta(days=10)).year
        contexto_temporal = f"expectations and early signals for {proximo_mes} {anio_objetivo}"
    else:
        contexto_temporal = f"current market drivers and volatility for {now.strftime('%B %Y')}"

    # Buscamos por "catalizadores" y "sorpresas", no por etiquetas fijas
    query = (
        f"{contexto_temporal}, "
        "institutional rebalancing and dark pool activity, "
        "breaking financial news with immediate price impact, "
        "unusual options flow and market sentiment shifts"
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
        print(f"[*] Escaneando ventana de impacto: {contexto_temporal}...")
        r = requests.post("https://api.tavily.com/search", json=payload, timeout=30)
        return r.json().get("results", [])
    except Exception as e:
        print(f"[!] Error: {e}")
        return []

def select_diverse_news(news_results):
    final_selection = []
    temas_vistos = set()

    # Mezclamos un poco para no coger siempre lo mismo si hay muchos resultados
    random.shuffle(news_results)

    for item in news_results:
        title = item.get("title", "")
        content = item.get("content", "")
        text = (title + " " + content).lower()

        # 1. Filtro de Datos: Priorizamos noticias con movimiento numérico
        has_numbers = any(char.isdigit() for char in text)
        if not has_numbers:
            continue

        # 2. Nueva lógica de categorización más amplia
        if any(x in text for x in ["gold", "oro", "silver", "plata"]):
            tema = "metales"
        elif any(x in text for x in ["gdp", "pib", "growth", "crecimiento"]):
            tema = "crecimiento"
        elif any(x in text for x in ["fed", "ecb", "bce", "rates", "tipos", "powell", "lagarde"]):
            tema = "bancos_centrales"
        elif any(x in text for x in ["altman", "musk", "saylor", "burry", "buffett", "nvidia", "apple", "tesla", "openai"]):
            # Capturamos a los "Market Movers" que mencionabas
            tema = "market_movers"
        elif any(x in text for x in ["ai", "ia", "tech", "tecnología", "breakthrough", "disrupción"]):
            tema = "disrupcion_tech"
        else:
            tema = "otros_impacto"

        # 3. Solo añadimos si el tema no está repetido en este ciclo
        if tema not in temas_vistos:
            final_selection.append(item)
            temas_vistos.add(tema)

        # Límite de 3 noticias por hora para no saturar
        if len(final_selection) >= 3:
            break

    return final_selection

def generate_expert_post(client: OpenAI, news_content: str, source_name: str):
    suggested_handle = _guess_source_handle(source_name) or source_name
    prompt = f"""
NOTICIA: {news_content}
MEDIO: {source_name}
HANDLE_SUGERIDO: {suggested_handle}

TAREA: Devuelve una respuesta dividida en 2 partes usando EXACTAMENTE el separador ###.

PARTE 1 (IMAGEN):
- Análisis macro corto + dato(s) numéricos + Probabilidad específica.
- NO incluyas hashtags ni fuentes aquí.

###

PARTE 2 (POST PARA X):
- Una PREGUNTA que se entienda sola, incluyendo el contexto y la cifra clave (ej: "¿Es realista que el Bitcoin alcance los 150K tras este último movimiento de Saylor?").
- 1 línea con la fuente: "Fuente: {suggested_handle}".
- 1 línea con 1 o 2 hashtags (trending).

REGLAS:
1. IDIOMA: Español de España.
2. AUTONOMÍA: El post debe entenderse sin mirar la imagen. Prohibido usar "esta noticia" o "¿llegará a ese precio?". Nombra el precio y el activo.
3. ESTILO: Analista senior, directo y provocador.
"""
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "Generas contenido financiero de alto impacto para X. Tus posts deben ser autosuficientes y generar debate inmediato."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return None

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


def _wrap_text(text: str, draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont, max_width: int):
    words = text.split()
    if not words:
        return [""]

    lines = []
    current = words[0]
    for word in words[1:]:
        test_line = f"{current} {word}"
        width = draw.textbbox((0, 0), test_line, font=font)[2]
        if width <= max_width:
            current = test_line
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _extract_probability_line(text: str) -> str:
    for line in text.splitlines():
        if "probabilidad" in line.lower():
            return line.strip()
    match = re.search(r"(Probabilidad[^.\n]*)(?:[.\n]|$)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return "Probabilidad: N/D"


def _extract_hashtags(text: str, max_count: int = 2) -> list[str]:
    tags = re.findall(r"#([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9_]+)", text)
    unique: list[str] = []
    seen = set()
    for tag in tags:
        normalized = f"#{tag}"
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(normalized)
        if len(unique) >= max_count:
            break
    return unique


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

def _compose_short_post(summary: str, source: str, hashtags: list[str]) -> str:
    parts: list[str] = []
    summary = " ".join((summary or "").split()).strip()
    if summary:
        parts.append(summary)
    source = (source or "").strip()
    if source:
        parts.append(f". Fuente: {source}")
    tags = [t for t in (hashtags or []) if t]
    if tags:
        parts.append(" ".join(tags[:2]))
    return "\n".join(parts).strip()


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
    return paragraph or "Actualización macro."


def _extract_very_brief_summary(macro_analysis: str, max_chars: int = 120) -> str:
    text = " ".join((macro_analysis or "").split()).strip()
    if not text:
        return "Actualización macro."

    sentences = re.split(r"(?<=[.!?])\s+", text)
    candidate = (sentences[0] or "").strip() if sentences else text
    if not candidate:
        candidate = text

    if len(candidate) <= max_chars:
        return candidate
    truncated = candidate[: max_chars - 1].rstrip()
    return truncated + "…"


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


def _extract_summary_word(text: str, fallback: str = "") -> str:
    fallback = (fallback or "").strip()
    if fallback and len(fallback.split()) == 1:
        return re.sub(r"^[@#]+", "", fallback).upper()

    hashtags = re.findall(r"#([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9_]+)", text)
    if hashtags:
        return hashtags[0].upper()

    lowered = text.lower()
    topic_map = [
        ("fed", "FED"),
        ("powell", "FED"),
        ("bce", "BCE"),
        ("ecb", "BCE"),
        ("pib", "PIB"),
        ("gdp", "PIB"),
        ("inflación", "INFLACIÓN"),
        ("inflation", "INFLACIÓN"),
        ("tipos", "TIPOS"),
        ("rates", "TIPOS"),
        ("oro", "ORO"),
        ("gold", "ORO"),
        ("plata", "PLATA"),
        ("silver", "PLATA"),
        ("arancel", "ARANCELES"),
        ("tariff", "ARANCELES"),
        ("recesión", "RECESIÓN"),
        ("recession", "RECESIÓN"),
    ]
    for needle, label in topic_map:
        if needle in lowered:
            return label

    return "MACRO"


def _extract_question_text(text: str) -> str:
    candidates = []
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        if cleaned.lower().startswith("fuente:"):
            continue
        if "?" in cleaned or "¿" in cleaned:
            candidates.append(cleaned)

    if candidates:
        return candidates[-1]

    last_q = text.rfind("?")
    if last_q == -1:
        return "¿Cómo lo está descontando el mercado?"
    start = text.rfind("¿", 0, last_q)
    if start == -1:
        start = max(text.rfind("\n", 0, last_q), 0)
    return text[start : last_q + 1].strip()


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
        return "Actualización macro"

    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    headline = " ".join(sentences[:2]).strip()
    return headline[:170].rstrip()


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

    highlight = random.choice(
        [
            (0, 255, 153),  # #00FF99 (verde neón)
            (255, 165, 0),  # #FFA500 (naranja)
            (255, 59, 48),  # #FF3B30 (rojo)
            (0, 163, 255),  # #00A3FF (azul)
        ]
    )
    base = (255, 255, 255)

    text = " ".join((image_text or "").split()).strip()
    text = _strip_analysis_prefix(text)
    if not text:
        text = "Actualización macro."

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
    combined = " ".join((macro_analysis or "").split()).strip()
    prob = " ".join((probability or "").split()).strip()
    if prob:
        combined = f"{combined} {prob}".strip()
    create_infographic(
        image_text=combined,
        output_path=output_path,
    )


def build_macro_drafts():
    """Orquesta el flujo macro y devuelve borradores listos para revision."""
    client = OpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com"
    )

    raw_news = get_hot_macro_news()
    if not raw_news:
        return []

    diverse_news = select_diverse_news(raw_news)
    print(f"[*] Analizando {len(diverse_news)} eventos de alto impacto.")

    drafts = []
    for item in diverse_news:
        url = item.get("url", "")
        source_name = url.split("//")[-1].split("/")[0].replace("www.", "")

        post = generate_expert_post(client, item.get("content", ""), source_name)
        if post:
            post = _strip_analysis_prefix(post)
            drafts.append(
                {
                    "ai_text": post,
                    "title": _extract_context_headline(post),
                }
            )

        time.sleep(2)

    return drafts
