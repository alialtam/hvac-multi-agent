#!/usr/bin/env bash
# One command on a fresh Ubuntu server (the Azure VM). Run it again later to update:
#   curl -fsSL https://raw.githubusercontent.com/alialtam/hvac-multi-agent/main/deploy/setup.sh -o setup.sh && bash setup.sh
set -euo pipefail
REPO_URL="${REPO_URL:-https://github.com/alialtam/hvac-multi-agent.git}"
DIR="$HOME/hvac-multi-agent"

echo "== 1/5 Docker and git"
command -v git >/dev/null || { sudo apt-get update -qq && sudo apt-get install -y -qq git; }
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sudo sh

echo "== 2/5 Swap space (small servers need it to build the dashboard)"
if [ -z "$(swapon --show)" ]; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap -q /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

echo "== 3/5 Code"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull --ff-only; else git clone -q "$REPO_URL" "$DIR"; fi
cd "$DIR"

echo "== 4/5 Settings"
if [ ! -f deploy/.env ]; then
  read -rp "DNS name of this server (e.g. hvac-ahd.centralindia.cloudapp.azure.com): " SITE
  read -rsp "OpenAI API key (press Enter for rules only): " KEY; echo
  PROVIDER=openai; [ -z "$KEY" ] && PROVIDER=rules
  TOKEN="ahd-$(openssl rand -hex 3)"
  umask 077
  cat > deploy/.env <<EOT
SITE_ADDRESS=$SITE
DEMO_TOKEN=$TOKEN
LLM_PROVIDER=$PROVIDER
OPENAI_API_KEY=$KEY
OPENAI_MODEL=gpt-4o-mini
SIM_START=09:00
EOT
  echo "Saved deploy/.env (readable only by you)."
fi
SITE=$(grep '^SITE_ADDRESS=' deploy/.env | cut -d= -f2)
TOKEN=$(grep '^DEMO_TOKEN=' deploy/.env | cut -d= -f2)

echo "== 5/5 Build and start (first time 5-10 minutes)"
sudo docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build
sudo docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps

cat <<EOT

Done. Open:  https://$SITE
Demo access key (for "Unlock controls"):  $TOKEN
Health check: https://$SITE/api/health
Logs:         sudo docker compose -f ~/hvac-multi-agent/deploy/docker-compose.yml logs -f backend
Update later: bash ~/hvac-multi-agent/deploy/setup.sh
EOT
