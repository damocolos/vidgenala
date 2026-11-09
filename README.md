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

2. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

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
mkdir -p data references_library && sudo chown 1000:1000 data references_library
docker compose up --build
```
The container runs as UID 1000, so the mounted `data/` folder must be writable by that user.
`NALARKA_DATA_DIR` (default `./data`) controls where the DB, uploads, renders and the encryption key live;
`NALARKA_KEY_FILE` overrides the key file location (an existing `data/.secret_key` is always reused).
