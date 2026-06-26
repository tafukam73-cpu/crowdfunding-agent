FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 依存ライブラリのインストール（レイヤーキャッシュのため先にコピー）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Playwright のブラウザ本体と OS 依存をインストール（headless Chromium）
RUN playwright install --with-deps chromium

# アプリ本体
COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
