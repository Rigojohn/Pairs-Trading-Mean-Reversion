# CryptoPairsBot runtime image: the paper bot and the dashboard (live mode will use the same image).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC

WORKDIR /app

# The dashboard's requirements include the bot's (-r requirements-bot.txt).
COPY requirements-bot.txt requirements-dashboard.txt ./
RUN pip install --no-cache-dir -r requirements-dashboard.txt

# Streamlit reads the dashboard theme from /app/.streamlit/config.toml.
COPY .streamlit ./.streamlit
COPY cryptopairs ./cryptopairs

# Unprivileged user; state/, logs/ and data/ are mounted from the host (see docker-compose.yml).
RUN useradd --uid 1000 --create-home bot && mkdir -p state logs runs config data && chown -R bot:bot /app
USER bot

EXPOSE 8501

# Exec form: Python is PID 1 and receives SIGTERM, so `docker stop` triggers the bot's
# clean shutdown (state is saved, the session is closed).
ENTRYPOINT ["python", "-m", "cryptopairs"]
CMD ["--profile", "kalman", "paper", "--pairs", "runs/kalman/pairs.yaml"]
