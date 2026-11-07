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
   git clone <repo-url>
   cd nalarka-video-generation
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

Launch the desktop UI:
```bash
python nalarka_local.py
```

