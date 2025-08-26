# Python 3.11の軽量イメージをベースにする
FROM python:3.11-slim

# コンテナ内の作業ディレクトリを設定
WORKDIR /app

# まずrequirements.txtだけをコピーして、キャッシュを有効活用する
COPY requirements.txt .

# Pythonライブラリをインストール
RUN pip install --no-cache-dir -r requirements.txt

# ★★★これが重要★★★
# Playwrightのブラウザ本体と、必要なシステムライブラリ(--with-deps)をまとめてインストール
RUN playwright install --with-deps

# 残りのアプリケーションコードをコピー
COPY . .

# アプリを実行するコマンド
# (もしファイル名が test.py の場合は、下の app.py を test.py に変更してください)
CMD ["python", "app.py"]