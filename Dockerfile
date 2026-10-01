# CryptoPairsBot runtime image (paper trading; live mode will use the same image).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC

WORKDIR /app

COPY requirements-bot.txt .
RUN pip install --no-cache-dir -r requirements-bot.txt

COPY cryptopairs ./cryptopairs

# Unprivileged user; state/ and logs/ are mounted from the host (see docker-compose.yml).
RUN useradd --uid 1000 --create-home bot && mkdir -p state logs runs config && chown -R bot:bot /app
USER bot

# Exec form: Python is PID 1 and receives SIGTERM, so `docker stop` triggers the bot's
# clean shutdown (state is saved, the session is closed).
ENTRYPOINT ["python", "-m", "cryptopairs"]
CMD ["--profile", "kalman", "paper", "--pairs", "runs/kalman/pairs.yaml"]
