#!/usr/bin/env bash
# Copy the code to the server and (re)start the bot and the dashboard there.
# The server keeps its state/, logs/ and data/: open positions survive an update.
#
#   ./deploy.sh              # server = "cryptobot" from ~/.ssh/config (DEPLOY.md, step 3)
#   ./deploy.sh root@1.2.3.4
set -euo pipefail

HOST="${1:-cryptobot}"
DIR=/opt/cryptopairs
cd "$(dirname "$0")"

rsync -avR --delete --exclude __pycache__ \
      cryptopairs config runs/kalman .streamlit \
      Dockerfile docker-compose.yml .dockerignore requirements-bot.txt requirements-dashboard.txt \
      "$HOST:$DIR/"

ssh "$HOST" "cd $DIR && mkdir -p state logs data && chown -R 1000:1000 state logs && docker compose up -d --build && docker compose ps"
