# Polymarket Bot (ES)

Bot en Python para generar posts en espanol sobre mercados activos de Polymarket, usando noticias en espanol y OpenAI para redactar el texto. Imprime los posts en consola.

## Requisitos

- Python 3.9+
- Claves API:
  - `TAVILY_API_KEY` (búsqueda de noticias)
  - `DEEPSEEK_API_KEY` (texto vía API compatible OpenAI)
  - `TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID` (si vas a enviar a Telegram)

## Instalacion

```bash
pip install -r requirements.txt
```

## Configuracion

Crea un archivo `.env` en la raiz del proyecto:

```
TAVILY_API_KEY=tu_clave_tavily
DEEPSEEK_API_KEY=tu_clave_deepseek
TELEGRAM_TOKEN=tu_token_telegram
TELEGRAM_CHAT_ID=tu_chat_id
```

Opcional:

```
TAVILY_TIME_RANGE=week
TAVILY_MAX_RESULTS=20
MAX_NEWS_AGE_DAYS=3
ALLOW_UNDATED_NEWS=1
ALLOW_STALE_NEWS=0
ALLOW_FALLBACK_SOURCES=1
TAVILY_DOMAIN_BIAS=1
TAVILY_DOMAIN_BIAS_RESULTS=10
```

## Uso

```bash
python3 scheduled_run.py
```

## Ejecucion programada (gratis) con GitHub Actions

Si te basta con ejecutar el bot 1 vez por hora entre las 08:00 y 21:00, puedes usar GitHub Actions (cron) sin servidor.

1. Sube el proyecto a un repo en GitHub.
2. En GitHub -> Settings -> Secrets and variables -> Actions:
   - Secrets:
     - `TAVILY_API_KEY`
     - `DEEPSEEK_API_KEY`
     - `TELEGRAM_TOKEN`
     - `TELEGRAM_CHAT_ID`
   - Variables (opcional):
     - `RUN_TZ` (ej: `Europe/Madrid`)
     - `RUN_START_HOUR` (default `8`)
     - `RUN_END_HOUR` (default `21`)
     - `MAX_DRAFTS` (ej: `4`)
     - `SEND_EMPTY_MESSAGE` (`1` para avisar cuando no haya borradores)
     - `TAVILY_TIME_RANGE` (`day|week|month|year`, default `week`)
     - `TAVILY_MAX_RESULTS` (default `20`)
     - `MAX_NEWS_AGE_DAYS` (default `3`)
     - `ALLOW_UNDATED_NEWS` (`1` para permitir resultados sin fecha, default `1`)
     - `ALLOW_STALE_NEWS` (`1` para permitir noticias antiguas, default `0`)
     - `ALLOW_FALLBACK_SOURCES` (`1` para permitir fuentes fuera de la lista preferida si faltan resultados, default `1`)
     - `TAVILY_DOMAIN_BIAS` (`1` para buscar tambien en dominios prioridad, default `1`)
     - `TAVILY_DOMAIN_BIAS_RESULTS` (default `10`)
3. El workflow ya está en `.github/workflows/scheduled-posts.yml` y corre cada hora; el script decide si está dentro de la ventana horaria.

Ejecucion local equivalente:

```bash
python3 scheduled_run.py
```

## Modo Telegram (si quieres dejarlo corriendo)

```bash
python3 telegram_controller.py
```

## Notas

- Filtra mercados por palabras clave relevantes para audiencia hispanohablante.
- Limita posts a 280 caracteres, con emojis, hashtags y pregunta final.
- Es un primer paso; se puede conectar luego a X/Twitter.
