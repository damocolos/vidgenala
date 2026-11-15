# Nalarka Motion Graphics Studio

An automated AI-driven motion graphics generator and vector animation studio powered by Python, PIL, and FFmpeg.

## Features

- **Procedural Motion Graphics Engine**: High-performance vector drawing primitives (smooth springs, typography posters, geometric shapes, speech bubbles, expressive characters).
- **AI-Powered Storyboard & Code Generation**: Uses LLMs to generate structured timelines and executable Python animation beats.
- **Verification Suite**: Automated contact sheets, visual variety scoring, frame-rate validation, and sanity checks.
- **TTS & Audio Integration**: Supports ElevenLabs and Edge TTS alongside synthetic ambient BGM generation.

## Prerequisites

- **Python 3.10+**
- **FFmpeg**: Must be installed and available in system `PATH` (`ffmpeg` and `ffprobe`).

## Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/damocolos/vidgenala.git
   cd vidgenala
   ```

2. **Install Python dependencies** (runtime only; use `requirements-dev.txt` if you also run the tests):
   ```bash
   pip install -r requirements.txt
   ```
   `requirements.lock` is a full `pip freeze` from one developer machine. Do not install it on a server.

3. **Configure Settings**:
   Copy the example config and add your credentials:
   ```bash
   cp config_nalarka.example.json config_nalarka.json
   ```
   Edit `config_nalarka.json` with your API keys and model preferences.

## Running the Application

### Web UI & API Server
Start the web server (auto-reload is OFF by default so code edits never interrupt a running render; set `NALARKA_RELOAD=1` for development):
```bash
python run_server.py
```
Or directly with Uvicorn (make sure to specify `--reload-dir nalarka`):
```bash
uvicorn nalarka.api.main:app --reload --reload-dir nalarka
```
Open your browser at `http://127.0.0.1:8000`.

### Desktop UI (Legacy)
```bash
python nalarka_local.py
```


## Controlling AI cost

Every AI request is saved in the database (Settings and the *AI requests* tab show them). In **Settings > AI budget and models per stage** you can:

- set a **token budget** and/or a **cost budget (USD)** per job. When a job reaches it, the job stops; everything already paid for is saved, and *Resume* continues from there. `0` means no limit.
- enter **prices** (USD per million tokens, copied from your provider's pricing page). The app never assumes a price: a model without one shows tokens only. With prices, the AI requests tab shows what a run cost and what reuse saved.
- choose a **different model per stage**: storyboard (Pass 1), beat code (Pass 2) and audit/rewrite. Empty means the provider's coder model.

Pressing *Render* again for the same topic reuses answers that earlier jobs already received for an identical request, so a failed run does not have to be paid for twice. Only beats without code are requested again.

Try a provider cheaply before a real run (a few tokens each; keys are never printed):

```bash
python scripts/smoke_llm.py              # every provider that has a key
python scripts/smoke_llm.py deepseek --models
```

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```
GitHub Actions runs the same on Ubuntu and Windows (`.github/workflows/tests.yml`). Some golden-image tests skip when the reference fonts are not installed.

## Using it from another device

By default the server only listens on `127.0.0.1`. To listen elsewhere (`NALARKA_HOST=0.0.0.0`, or inside Docker) `run_server.py` refuses to start unless you set `NALARKA_API_TOKEN` (16+ characters) and `NALARKA_ALLOWED_HOSTS`. See `.env.example`.
The token is sent with every request, so use **HTTPS**: put a reverse proxy in front (for example Caddy: `studio.example.com { reverse_proxy 127.0.0.1:8000 }`) and add `https://studio.example.com` to `NALARKA_ALLOWED_ORIGINS`. Do not expose plain `http://` over the internet.

## Keamanan lokal (env opsional)

| Variabel | Fungsi |
|---|---|
| `NALARKA_ALLOWED_HOSTS` | Host tambahan yang boleh mengakses API (default: localhost, 127.0.0.1, ::1) |
| `NALARKA_ALLOWED_ORIGINS` | Origin tambahan (mis. `https://studio.example.com`) |
| `NALARKA_API_TOKEN` | Jika diisi, `/api/*` butuh token (`X-Nalarka-Token`, `Bearer`, atau cookie dari `GET /auth?token=...`) |
| `NALARKA_DATA_DIR` | Folder data (default `data`) |
| `NALARKA_MEDIA_DIRS` | Folder tambahan (dipisah `os.pathsep`) untuk `vo_path`/`ref_path`/`bgm_path` |

`vo_path`, `ref_path`, `bgm_path`, dan `/api/renders/preview-image` hanya menerima file di dalam `data/`, folder library, atau `NALARKA_MEDIA_DIRS`.

## Docker

```bash
cp .env.example .env          # then set NALARKA_API_TOKEN (required in Docker)
mkdir -p data references_library && sudo chown 1000:1000 data references_library
docker compose up --build
```
Open `http://127.0.0.1:8000/auth?token=<your token>` once to sign the browser in.
The container runs as UID 1000, so the mounted `data/` folder must be writable by that user.
`NALARKA_DATA_DIR` (default `./data`) controls where the DB, uploads, renders and the encryption key live;
`NALARKA_KEY_FILE` overrides the key file location (an existing `data/.secret_key` is always reused).
