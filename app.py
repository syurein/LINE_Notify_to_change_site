import gradio as gr
import time
import requests
import threading
import json
import os
import difflib
from playwright.sync_api import sync_playwright, Page
import pandas as pd
from datetime import datetime
import subprocess

# --- JSONデータベース設定 ---
# Dockerボリュームにデータを永続化するための設定
# 環境変数 'DATA_DIR' で永続化するディレクトリを指定（デフォルトは '/data'）
DATA_DIR = os.environ.get('DATA_DIR', '/data')
# ディレクトリが存在しない場合は作成
os.makedirs(DATA_DIR, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, "monitoring_db.json")
db_lock = threading.Lock() # データベースファイルへのアクセスを制御するためのロック

# --- JSONファイル操作関数 ---
def load_targets():
    """JSONファイルから監視リストを読み込む"""
    with db_lock: # ロックを取得
        if not os.path.exists(DB_FILE):
            return []
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []

def save_targets(targets):
    """監視リストをJSONファイルに保存する"""
    with db_lock: # ロックを取得
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(targets, f, indent=4, ensure_ascii=False)

def init_json_db():
    """JSONデータベースファイルを初期化する"""
    if not os.path.exists(DB_FILE):
        save_targets([])

# --- グローバル変数 ---
app_state = {
    "log_history": "アプリを起動しました。\n"
}

# --- LINE Messaging APIへの通知機能 ---
def send_message(channel_token, user_id, message):
    """単一のテキストメッセージをLINE Messaging API経由で送信する"""
    push_api_url = 'https://api.line.me/v2/bot/message/push'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {channel_token}'
    }
    payload = {
        'to': user_id,
        'messages': [{'type': 'text', 'text': message}]
    }
    try:
        response = requests.post(push_api_url, headers=headers, json=payload)
        response.raise_for_status()
        return f"LINEメッセージの送信成功"
    except Exception as e:
        return f"LINEメッセージの送信に失敗: {e}"

def send_long_message(channel_token, user_id, message):
    """長文メッセージを分割して送信する"""
    global app_state
    max_length = 4800  # LINEのAPI制限より少し短く設定
    full_log = ""

    if len(message) <= max_length:
        log = send_message(channel_token, user_id, message)
        full_log += log + "\n"
    else:
        # メッセージを分割
        parts = [message[i:i+max_length] for i in range(0, len(message), max_length)]
        for i, part in enumerate(parts):
            # 分割したことを示すヘッダーを追加
            part_header = f"【{i+1}/{len(parts)}】\n"
            log = send_message(channel_token, user_id, part_header + part)
            full_log += log + "\n"
            time.sleep(1) # API制限を避けるための短い待機
            
    app_state["log_history"] += full_log
    return full_log


# --- 個別のURLをチェックする関数 ---
def perform_scrape_and_check(target: dict, page: Page):
    """単一のターゲットURLをスクレイピングし、変更をチェックして新しい内容を返す"""
    global app_state
    url = target['url']
    mode = target['mode']
    last_content = target.get('last_content', '') # Noneではなく空文字をデフォルトに
    notify_on_check = target.get('notify_on_check', False)
    attach_content = target.get('attach_content', False) # 新しいオプションを取得
    
    channel_token = target.get('channel_token')
    user_id = target.get('user_id')

    log_message = f"チェック中: {url}\n"
    print(log_message)
    app_state["log_history"] += log_message
    
    new_content = ""
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=60000)
        
        if mode == "エルメスモード (特定要素)":
            time.sleep(10)
            elements = page.locator('div.product-item').all_text_contents()
            new_content = "\n".join(line.strip() for el in elements for line in el.strip().split('\n') if line.strip())
        elif mode == "メルカリモード (商品リスト)":
            time.sleep(10)
            elements = page.locator('[class*="imageContainer"]').evaluate_all(
                "(elements) => elements.map(e => e.getAttribute('aria-label'))"
            )
            new_content = "\n".join(elements)
        else: # 通常モード
            time.sleep(10)
            new_content = page.locator('body').text_content()
            
        site_name = url.split('/')[2]
        
        # last_contentがNoneの場合の初回実行
        if target.get('last_content') is None:
            log_message = f"初回コンテンツ取得: {url}\n"
            print(log_message)
            app_state["log_history"] += log_message
            if notify_on_check:
                message = f"【監視開始】\nサイト「{site_name}」の監視を開始しました。\n{url}"
                if attach_content:
                    content_summary = ""
                    if mode in ["メルカリモード (商品リスト)", "エルメスモード (特定要素)"] and new_content:
                        content_summary = f"\n\n--- 現在のアイテム一覧 ---\n{new_content}"
                    message = f"【監視開始】\nサイト「{site_name}」の監視を開始しました。{content_summary}\n\n{url}"
                send_long_message(channel_token, user_id, message)

        elif last_content != new_content:
            log_message = f"変更を検知！: {url}\n"
            print(log_message)
            app_state["log_history"] += log_message
            
            message = f"【更新通知】\nサイト「{site_name}」で変化を検知しました！\nすぐに確認してください！\n{url}"
            if attach_content:
                old_lines = last_content.splitlines()
                new_lines = new_content.splitlines()
                diff = difflib.unified_diff(
                    old_lines, new_lines, fromfile='変更前', tofile='変更後', lineterm=''
                )
                # --- と +++ ヘッダー行を除き、実際の変更行のみを抽出
                diff_lines = [line for line in diff if line.startswith('+') or line.startswith('-')][2:]
                diff_output = "\n".join(diff_lines)

                if not diff_output:
                    message = (
                        f"【更新通知】\nサイト「{site_name}」で変更を検知しました（差分表示なし）。\n\n"
                        f"--- 変更前 ---\n{last_content}\n\n"
                        f"--- 変更後 ---\n{new_content}\n\n"
                        f"すぐに確認してください！\n{url}"
                    )
                else:
                    message = (
                        f"【更新通知】\nサイト「{site_name}」で変化を検知しました！\n\n"
                        f"--- 変更箇所 ---\n{diff_output}\n\n"
                        f"すぐに確認してください！\n{url}"
                    )
            send_long_message(channel_token, user_id, message)

        else:
            log_message = f"変更なし: {url}\n"
            print(log_message)
            app_state["log_history"] += log_message
            if notify_on_check:
                message = f"【定期チェック完了】\nサイト「{site_name}」をチェックしました (変更なし)。\n{url}"
                if attach_content:
                    summary_for_no_change = ""
                    if mode in ["メルカリモード (商品リスト)", "エルメスモード (特定要素)"] and new_content:
                        top_items = "\n".join(new_content.split('\n')[:5])
                        summary_for_no_change = f"\n\n--- 最新上位5件 ---\n{top_items}"
                    message = f"【定期チェック完了】\nサイト「{site_name}」をチェックしました (変更なし)。{summary_for_no_change}\n\n{url}"
                send_long_message(channel_token, user_id, message)
        
        return new_content


    except Exception as e:
        log_message = f"エラー発生 ({url}): {e}\n"
        print(log_message)
        app_state["log_history"] += log_message
        return None

# --- 永続的な監視ループ ---
def master_monitoring_loop():
    """アプリのバックグラウンドで永続的に実行されるマスターループ"""
    global app_state
    log_message = "監視マスタースレッドを開始しました。\n"
    print(log_message)
    app_state["log_history"] += log_message

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True) # 通常はヘッドレスで実行
        context = browser.new_context(
             user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        )
        
        try:
            while True:
                # チェックが必要なターゲットのリストを取得
                targets_to_check = [
                    t for t in load_targets()
                    if time.time() - t.get('last_checked', 0) > t['interval']
                ]

                for target in targets_to_check:
                    page = context.new_page()
                    try:
                        # スクレイピング処理（時間がかかる可能性がある）
                        new_content = perform_scrape_and_check(target, page)
                        
                        # --- 競合回避のための修正 ---
                        # 1. スクレイピング完了後、DBから最新のリストを再度読み込む
                        all_targets_now = load_targets()
                        
                        # 2. 最新のリストから更新対象のアイテムを探す
                        target_to_update = next((t for t in all_targets_now if t['id'] == target['id']), None)

                        # 3. アイテムが見つからない場合：
                        #    スクレイピング中にUIから削除されたことを意味するので、何もしない
                        if not target_to_update:
                            log_message = f"ID {target['id']} はチェック中に削除されたため、更新をスキップします。\n"
                            print(log_message)
                            app_state["log_history"] += log_message
                            continue

                        # 4. アイテムが見つかった場合、更新する
                        target_to_update['last_checked'] = time.time()
                        if new_content is not None:
                            target_to_update['last_content'] = new_content
                        
                        # 5. 更新後のリスト全体をDBに保存する
                        save_targets(all_targets_now)

                    finally:
                        page.close()

                # 次のチェックサイクルまで待機
                time.sleep(5)
        finally:
            browser.close()

# --- Gradio UIイベントハンドラ ---
def add_target(url, channel_token, user_id, interval, mode, notify_on_check, attach_content):
    """監視対象をJSONに追加する"""
    if not all([url, channel_token, user_id, interval, mode]):
        gr.Warning("すべてのフィールドを入力してください。")
        return get_targets_as_dataframe()
    if not url.startswith('http'):
        gr.Warning("有効なURLを入力してください。")
        return get_targets_as_dataframe()

    targets = load_targets()
    
    if any(t['url'] == url for t in targets):
        gr.Warning("このURLは既に追加されています。")
        return get_targets_as_dataframe()

    new_id = max([t['id'] for t in targets] + [0]) + 1
    
    new_target = {
        "id": new_id,
        "url": url,
        "channel_token": channel_token,
        "user_id": user_id,
        "mode": mode,
        "interval": int(interval),
        "notify_on_check": notify_on_check,
        "attach_content": attach_content, # オプションを保存
        "last_content": None,
        "last_checked": 0
    }
    targets.append(new_target)
    save_targets(targets)
    gr.Info(f"監視対象を追加しました: {url}")
    
    return get_targets_as_dataframe()

def delete_target_by_id(target_id_to_delete):
    """指定されたIDの監視対象を削除する"""
    if target_id_to_delete is None or target_id_to_delete == '':
        gr.Warning("削除するIDを入力してください。")
        return get_targets_as_dataframe()
    
    try:
        target_id_to_delete_int = int(target_id_to_delete)
    except (ValueError, TypeError):
        gr.Warning("IDは数値で入力してください。")
        return get_targets_as_dataframe()
        
    targets = load_targets()
    
    new_targets = [t for t in targets if t['id'] != target_id_to_delete_int]
    
    if len(new_targets) == len(targets):
        gr.Warning(f"ID {target_id_to_delete_int} が見つかりませんでした。")
    else:
        save_targets(new_targets)
        gr.Info(f"ID {target_id_to_delete_int} を削除しました。")
        
    return get_targets_as_dataframe()

def get_targets_as_dataframe():
    """JSONから現在の監視リストを取得し、DataFrameとして返す"""
    targets = load_targets()
    if not targets:
        return pd.DataFrame(columns=['ID', 'URL', 'モード', '間隔(秒)', '最終チェック日時', '毎回通知', '内容を添付'])

    df = pd.DataFrame(targets)
    # 古いデータとの互換性
    if 'notify_on_check' not in df.columns:
        df['notify_on_check'] = False
    if 'attach_content' not in df.columns:
        df['attach_content'] = False

    df = df[['id', 'url', 'mode', 'interval', 'last_checked', 'notify_on_check', 'attach_content']]
    df['last_checked'] = df['last_checked'].apply(
        lambda ts: datetime.fromtimestamp(ts).strftime('%Y-m-%d %H:%M:%S') if ts > 0 else "未チェック"
    )
    df['notify_on_check'] = df['notify_on_check'].apply(lambda x: "はい" if x else "いいえ")
    df['attach_content'] = df['attach_content'].apply(lambda x: "はい" if x else "いいえ")
    
    df.rename(columns={
        'id':'ID', 'url':'URL', 'mode':'モード', 
        'interval':'間隔(秒)', 'last_checked':'最終チェック日時',
        'notify_on_check': '毎回通知',
        'attach_content': '内容を添付'
    }, inplace=True)
    return df

def get_logs():
    """ログ表示エリアを定期的に更新する"""
    log_lines = app_state["log_history"].split('\n')
    return "\n".join(log_lines[-50:])

# --- Gradio UIの構築 ---
with gr.Blocks(theme=gr.themes.Soft(), title="Web更新通知ツール") as app:
    gr.Markdown("# Webサイト更新通知ツール (Messaging API版)")
    gr.Markdown("複数の監視対象を登録でき、ブラウザを閉じても監視を継続します。")

    with gr.Row():
        with gr.Column(scale=2):
            gr.Markdown("### 監視リスト")
            targets_display = gr.DataFrame(
                value=get_targets_as_dataframe, 
                interactive=False
            )
            refresh_btn = gr.Button("リストを手動更新")
            
            with gr.Accordion("新しい監視対象を追加", open=True):
                url_input = gr.Textbox(label="監視対象URL", placeholder="https://www.hermes.com/jp/ja/...")
                channel_token_input = gr.Textbox(label="LINE Channel Access Token", type="password", placeholder="LINE Developersコンソールから発行した長期トークン")
                user_id_input = gr.Textbox(label="LINE User ID", placeholder="あなたのユーザーID (Uから始まる文字列)")
                with gr.Row():
                    interval_input = gr.Slider(minimum=10, maximum=1800, value=600, step=10, label="監視間隔 (秒)")
                    mode_input = gr.Radio(
                        ["通常モード (ページ全体)", "エルメスモード (特定要素)", "メルカリモード (商品リスト)"], 
                        label="監視モード", 
                        value="通常モード (ページ全体)"
                    )
                with gr.Row():
                    notify_on_check_input = gr.Checkbox(label="チェック毎に通知する")
                    attach_content_input = gr.Checkbox(label="通知に内容を添付する", value=True) # UIにチェックボックスを追加
                
                add_btn = gr.Button("リストに追加", variant="primary")

            with gr.Accordion("監視対象を削除", open=False):
                delete_id_input = gr.Number(label="削除したい項目のIDを入力してください", precision=0)
                delete_btn = gr.Button("このIDの項目を削除", variant="stop")

        with gr.Column(scale=1):
            gr.Markdown("### 実行ログ")
            log_output = gr.Textbox(label=" ", lines=25, interactive=False, autoscroll=True)

    # イベントリスナー
    add_btn.click(
        fn=add_target,
        inputs=[url_input, channel_token_input, user_id_input, interval_input, mode_input, notify_on_check_input, attach_content_input],
        outputs=[targets_display]
    )
    delete_btn.click(fn=delete_target_by_id, inputs=[delete_id_input], outputs=[targets_display])
    refresh_btn.click(fn=get_targets_as_dataframe, inputs=None, outputs=[targets_display])
    
    # --- 定期的な更新処理 ---
    # 2秒ごとにログを更新
    gr.Timer(2).tick(get_logs, None, log_output)
    # 10秒ごとに監視リストのDataFrameを更新
    gr.Timer(10).tick(get_targets_as_dataframe, None, targets_display)


# --- アプリケーションの起動 ---
if __name__ == "__main__":
    # Docker環境では、PlaywrightのインストールはDockerfileで行うため、
    # このPythonスクリプト内でのインストール処理は不要です。

    print("JSONデータベースを初期化します...")
    init_json_db()
    print("初期化が完了しました。")

    # バックグラウンドで監視スレッドを開始
    monitor_thread = threading.Thread(target=master_monitoring_loop, daemon=True)
    monitor_thread.start()
    
    # --- Dockerのための起動設定 ---
    # Dockerfileと合わせて、以下のコマンドでコンテナを起動します。
    # 1. イメージのビルド:
    #    docker build -t web-monitor-app .
    #
    # 2. コンテナの起動 (データ永続化のためボリュームを使用):
    #    docker run -d -p 7860:7860 -v "$(pwd)/my_data:/data" --name web-monitor web-monitor-app
    #    - "-d": バックグラウンドで実行
    #    - "-p 7860:7860": ホストのポート7860をコンテナのポート7860にマッピング
    #    - "-v "$(pwd)/my_data:/data"": ホストのカレントディレクトリ下の'my_data'フォルダを
    #      コンテナの'/data'ディレクトリにマッピングします。
    #      'my_data'フォルダは自動で作成されます。ここに'monitoring_db.json'が保存されます。
    
    print("Gradioアプリを起動します...")
    # コンテナ内で外部からのアクセスを受け付けるために server_name="0.0.0.0" を指定
    # ポートはGradioのデフォルト(7860)を使用
    app.launch(server_name="0.0.0.0", server_port=int(os.getenv('PORT', 7860)))
