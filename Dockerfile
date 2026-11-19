# Bot Telegram + MetaApi chạy trên Linux / cloud.
#   docker build -t gold-bot .
#   docker run -d --name gold-bot --restart unless-stopped --env-file .env -v gold-bot-live:/app/live gold-bot
FROM python:3.11-slim
ENV PYTHONUTF8=1 PYTHONUNBUFFERED=1 MPLBACKEND=Agg BROKER=metaapi
WORKDIR /app
COPY requirements-bot.txt .
RUN pip install --no-cache-dir -r requirements-bot.txt
COPY config.yaml ./
COPY broker ./broker
COPY telegram_bot ./telegram_bot
COPY clone_repo/gold_engine.py clone_repo/gold_repos_m5.py ./clone_repo/
RUN mkdir -p live
CMD ["python", "telegram_bot/bot.py"]
