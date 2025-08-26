# Python 3.11の軽量イメージをベースにする
FROM python:3.11-slim

# コンテナ内の作業ディレクトリを設定
WORKDIR /app

# ★★★これが重要★★★
# Playwrightが必要とするシステムライブラリを、正しい名前で先に手動インストールする
RUN apt-get update && apt-get install -y \
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libatspi2.0-0 \
    libgbm1 \
    libasound2 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    libpango-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libgtk-3-0 \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# requirements.txtをコピーしてPythonライブラリをインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# システムライブラリはインストール済みなので、ブラウザ本体だけをインストール
RUN playwright install chromium

# 残りのアプリケーションコードをコピー
COPY . .

# アプリを実行するコマンド
# (あなたのファイル名が test.py の場合は、下の app.py を test.py に変更してください)
CMD ["python", "app.py"]