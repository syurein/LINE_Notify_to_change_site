import gradio as gr
import time
import requests
import threading
import json
import os
from playwright.sync_api import sync_playwright, Page
import pandas as pd
from datetime import datetime

# --- JSONデータベース設定 ---
DB_FILE = "monitoring_db.json"

# --- JSONファイル操作関数 ---
def load_targets():
    """JSONファイルから監視リストを読み込む"""
    if not os.path.exists(DB_FILE):
        return []
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []

def save_targets(targets):
    """監視リストをJSONファイルに保存する"""
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

# --- ★★★ 修正点1: LINE Messaging APIへの通知機能に変更 ---
def send_message(channel_token, user_id, message):
    """指定されたユーザーにLINE Messaging API経由でプッシュメッセージを送信する"""
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
        # requests.postの引数としてjson=payloadを渡すと自動でJSON形式に変換してくれる
        response = requests.post(push_api_url, headers=headers, json=payload)
        response.raise_for_status()  # エラーがあれば例外を発生させる
        return f"LINEメッセージを送信しました。\n"
    except Exception as e:
        return f"LINEメッセージの送信に失敗しました: {e}\n"

# --- 個別のURLをチェックする関数 ---
def perform_scrape_and_check(target: dict, page: Page):
    """単一のターゲットURLをスクレイピングし、変更をチェックして新しい内容を返す"""
    global app_state
    url = target['url']
    mode = target['mode']
    last_content = target.get('last_content')
    notify_on_check = target.get('notify_on_check', False)
    
    # Messaging APIに必要な情報を取得
    channel_token = target.get('channel_token')
    user_id = target.get('user_id')

    log_message = f"チェック中: {url}\n"
    print(log_message)
    app_state["log_history"] += log_message
    
    new_content = ""
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=60000)

        if mode == "エルメスモード (特定要素)":
            elements = page.locator('div.product-item-meta').all_text_contents()
            new_content = "".join(elements)
        else:
            new_content = page.locator('body').text_content()
        
        site_name = url.split('/')[2]

        if last_content is None:
            log_message = f"初回コンテンツ取得: {url}\n"
            print(log_message)
            app_state["log_history"] += log_message
            if notify_on_check:
                message = f"【監視開始】\nサイト「{site_name}」の監視を開始しました。\n{url}"
                notification_log = send_message(channel_token, user_id, message)
                app_state["log_history"] += notification_log

        elif last_content != new_content:
            log_message = f"変更を検知！: {url}\n"
            print(log_message)
            app_state["log_history"] += log_message
            message = f"【更新通知】\nサイト「{site_name}」で変化を検知しました！\nすぐに確認してください！\n{url}"
            notification_log = send_message(channel_token, user_id, message)
            app_state["log_history"] += notification_log
        else:
            log_message = f"変更なし: {url}\n"
            print(log_message)
            app_state["log_history"] += log_message
            if notify_on_check:
                message = f"【定期チェック完了】\nサイト「{site_name}」をチェックしました (変更なし)。\n{url}"
                notification_log = send_message(channel_token, user_id, message)
                app_state["log_history"] += notification_log
        
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
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
             user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        )
        
        try:
            while True:
                all_targets = load_targets()
                db_updated = False
                
                targets_to_check = [
                    t for t in all_targets 
                    if time.time() - t.get('last_checked', 0) > t['interval']
                ]

                for target in targets_to_check:
                    page = context.new_page()
                    try:
                        new_content = perform_scrape_and_check(target, page)
                        
                        target_in_all = next((t for t in all_targets if t['id'] == target['id']), None)
                        if not target_in_all:
                            continue

                        target_in_all['last_checked'] = time.time()
                        db_updated = True

                        if new_content is not None:
                            target_in_all['last_content'] = new_content
                    
                    finally:
                        page.close()

                if db_updated:
                    save_targets(all_targets)

                time.sleep(5)
        finally:
            browser.close()

# --- Gradio UIイベントハンドラ ---
# --- ★★★ 修正点2: 引数と保存するデータにuser_idを追加 ---
def add_target(url, channel_token, user_id, interval, mode, notify_on_check):
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
        "channel_token": channel_token, # 項目名を変更
        "user_id": user_id,             # user_idを追加
        "mode": mode,
        "interval": int(interval),
        "notify_on_check": notify_on_check,
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
    
    target_id_to_delete = int(target_id_to_delete)
    targets = load_targets()
    
    new_targets = [t for t in targets if t['id'] != target_id_to_delete]
    
    if len(new_targets) == len(targets):
        gr.Warning(f"ID {target_id_to_delete} が見つかりませんでした。")
    else:
        save_targets(new_targets)
        gr.Info(f"ID {target_id_to_delete} を削除しました。")
        
    return get_targets_as_dataframe()

def get_targets_as_dataframe():
    """JSONから現在の監視リストを取得し、DataFrameとして返す"""
    targets = load_targets()
    if not targets:
        return pd.DataFrame(columns=['ID', 'URL', 'モード', '間隔(秒)', '最終チェック日時', '毎回通知'])

    df = pd.DataFrame(targets)
    if 'notify_on_check' not in df.columns:
        df['notify_on_check'] = False

    df = df[['id', 'url', 'mode', 'interval', 'last_checked', 'notify_on_check']]
    df['last_checked'] = df['last_checked'].apply(
        lambda ts: datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S') if ts > 0 else "未チェック"
    )
    df['notify_on_check'] = df['notify_on_check'].apply(lambda x: "はい" if x else "いいえ")
    
    df.rename(columns={
        'id':'ID', 'url':'URL', 'mode':'モード', 
        'interval':'間隔(秒)', 'last_checked':'最終チェック日時',
        'notify_on_check': '毎回通知'
    }, inplace=True)
    return df

def get_logs():
    """ログ表示エリアを定期的に更新する"""
    log_lines = app_state["log_history"].split('\n')
    return "\n".join(log_lines[-50:])

# --- Gradio UIの構築 ---
# --- ★★★ 修正点3: UIに入力項目を追加し、ラベルを分かりやすく変更 ---
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
            refresh_btn = gr.Button("リストを更新")
            
            with gr.Accordion("新しい監視対象を追加", open=True):
                url_input = gr.Textbox(label="監視対象URL", placeholder="https://www.hermes.com/jp/ja/...")
                # ラベルとプレースホルダーを変更
                channel_token_input = gr.Textbox(label="LINE Channel Access Token", type="password", placeholder="LINE Developersコンソールから発行した長期トークン")
                user_id_input = gr.Textbox(label="LINE User ID", placeholder="あなたのユーザーID (Uから始まる文字列)")
                with gr.Row():
                    interval_input = gr.Slider(minimum=10, maximum=600, value=60, step=10, label="監視間隔 (秒)")
                    mode_input = gr.Radio(
                        ["通常モード (ページ全体)", "エルメスモード (特定要素)"], 
                        label="監視モード", 
                        value="通常モード (ページ全体)"
                    )
                notify_on_check_input = gr.Checkbox(label="チェックするたびにLINEで通知する（変更がなくても通知が届きます）")
                add_btn = gr.Button("リストに追加", variant="primary")

            with gr.Accordion("監視対象を削除", open=False):
                delete_id_input = gr.Number(label="削除したい項目のIDを入力してください", precision=0)
                delete_btn = gr.Button("このIDの項目を削除", variant="stop")

        with gr.Column(scale=1):
            gr.Markdown("### 実行ログ")
            log_output = gr.Textbox(label=" ", lines=20, interactive=False, autoscroll=True)

    # イベントリスナー
    # inputsにuser_id_inputを追加
    add_btn.click(
        fn=add_target,
        inputs=[url_input, channel_token_input, user_id_input, interval_input, mode_input, notify_on_check_input],
        outputs=[targets_display]
    )
    delete_btn.click(fn=delete_target_by_id, inputs=[delete_id_input], outputs=[targets_display])
    refresh_btn.click(fn=get_targets_as_dataframe, inputs=None, outputs=[targets_display])
    
    gr.Timer(2).tick(get_logs, None, log_output)

# --- アプリケーションの起動 ---
if __name__ == "__main__":
    if not os.path.exists(os.path.expanduser('~/.cache/ms-playwright')):
        print("Playwrightのブラウザをインストールします...")
        os.system('playwright install')
        print("インストールが完了しました。")

    print("JSONデータベースを初期化します...")
    init_json_db()
    print("初期化が完了しました。")

    monitor_thread = threading.Thread(target=master_monitoring_loop, daemon=True)
    monitor_thread.start()
    
    app.launch()