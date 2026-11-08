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

# Create persistent storage directories
RUN mkdir -p /app/data /app/references_library

# Environment configuration
ENV PYTHONUNBUFFERED=1 \
    NALARKA_DB_PATH=/app/data/nalarka.db

EXPOSE 8000

# Health check using API endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

# Start FastAPI server via Uvicorn
CMD ["uvicorn", "nalarka.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
