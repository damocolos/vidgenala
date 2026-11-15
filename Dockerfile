FROM python:3.10-slim

# Install system dependencies (ffmpeg, ffprobe, fonts)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    fonts-freefont-ttf \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY nalarka/ /app/nalarka/
COPY legacy/ /app/legacy/
COPY scripts/ /app/scripts/
COPY run_server.py /app/run_server.py

# Create persistent storage directories and run as an unprivileged user
RUN useradd --create-home --uid 1000 nalarka \
    && mkdir -p /app/data /app/references_library \
    && chown -R nalarka:nalarka /app
USER nalarka

# Environment configuration
# Inside the container the server must listen on all interfaces; run_server.py then insists on
# NALARKA_API_TOKEN and NALARKA_ALLOWED_HOSTS (docker-compose.yml passes them). Publish the port on
# 127.0.0.1 only, or put an HTTPS reverse proxy in front of it.
ENV PYTHONUNBUFFERED=1 \
    NALARKA_HOST=0.0.0.0 \
    NALARKA_DATA_DIR=/app/data \
    NALARKA_DB_PATH=/app/data/nalarka.db

EXPOSE 8000

# Health check using API endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

# Start the server (refuses to run exposed without a token)
CMD ["python", "run_server.py"]
