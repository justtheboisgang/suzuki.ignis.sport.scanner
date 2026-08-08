# Ignis Sport Europe Hunter — production image.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # Store the SQLite DB on the persistent volume mounted at /app/data.
    # (Absolute sqlite URL uses four slashes.) Override via Railway env if needed.
    DATABASE_URL=sqlite:////app/data/ignis_hunter.db

WORKDIR /app

# System deps for lxml / Pillow.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libxml2-dev libxslt1-dev libjpeg-dev zlib1g-dev \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

# Persistent data dir (Railway mounts the volume here). We run as root so a
# root-owned mounted volume is always writable — this is a single-user hobby
# deployment; keep it simple and robust rather than fighting mount ownership.
RUN mkdir -p /app/data /app/logs

# ONE service: scheduler (4 daily scans) + dashboard together, on 0.0.0.0:$PORT.
# The DB is initialised and migrated automatically on start.
CMD ["python", "-m", "src.cli", "serve"]
