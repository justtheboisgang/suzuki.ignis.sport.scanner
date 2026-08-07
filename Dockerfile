# Ignis Sport Europe Hunter — production image.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps for lxml / Pillow.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libxml2-dev libxslt1-dev libjpeg-dev zlib1g-dev \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

# Non-root runtime user.
RUN useradd -m hunter && mkdir -p data logs && chown -R hunter:hunter /app
USER hunter

# Initialise DB + seed sources at build time is avoided (needs the volume);
# the entrypoint commands call init on start instead.

# Default command runs the scheduler (override to run the dashboard).
CMD ["python", "-m", "src.cli", "scheduler"]
