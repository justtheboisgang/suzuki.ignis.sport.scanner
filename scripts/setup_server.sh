#!/usr/bin/env bash
# ===========================================================================
# Ignis Sport Europe Hunter — one-shot VPS setup for Ubuntu/Debian.
# Installs Docker (if missing), prepares .env, builds and starts the stack.
# Contains NO secrets. Idempotent: safe to re-run.
#
# Usage:
#   git clone https://github.com/justtheboisgang/suzuki.ignis.sport.scanner
#   cd suzuki.ignis.sport.scanner
#   ./scripts/setup_server.sh
#   # then edit .env with your API keys and run:  docker compose up -d --build
# ===========================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Ignis Hunter server setup"

# --- 1. Docker -------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  echo "==> Installing Docker Engine + compose plugin..."
  sudo apt-get update -y
  sudo apt-get install -y ca-certificates curl gnupg
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg 2>/dev/null || true
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin
  sudo systemctl enable --now docker
else
  echo "==> Docker already installed: $(docker --version)"
fi

# --- 2. .env ---------------------------------------------------------------
if [ ! -f .env ]; then
  cp .env.production.example .env
  echo "==> Created .env from .env.production.example"
  echo "    !!! EDIT .env NOW and add ANTHROPIC_API_KEY + BRAVE_SEARCH_API_KEY."
else
  echo "==> .env already exists — leaving it untouched."
fi

# --- 3. Data/log dirs ------------------------------------------------------
mkdir -p data logs

# --- 4. Build --------------------------------------------------------------
echo "==> Building images..."
sudo docker compose build

cat <<'EOF'

===========================================================================
Setup complete.

NEXT STEPS
  1. Edit secrets:            nano .env
  2. Validate connectivity:   sudo docker compose run --rm scheduler python -m src.cli network-test
  3. First real full scan:    sudo docker compose run --rm scheduler python -m src.cli scan --force
  4. Start permanent stack:   sudo docker compose up -d
  5. Dashboard:               http://<SERVER_IP>:8000
  6. Follow logs:             sudo docker compose logs -f scheduler
===========================================================================
EOF
