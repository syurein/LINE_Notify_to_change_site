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
DATA_DIR = os.environ.get('DATA_DIR', '/data')
os.makedirs(DATA_DIR, exist_ok=True)
DB_FILE = os.path.join(DATA_DIR, "monitoring_db.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "app_settings.json")
db_lock = threading.Lock()

# --- JSONファイル操作関数 ---
def load_targets():
    """JSONファイルから監視リストを読み込む"""
    with db_lock:
        if not os.path.exists(DB_FILE):
            return []
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []

def save_targets(targets):
    """監視リストをJSONファイルに保存する"""
    with db_lock:
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(targets, f, indent=4, ensure_ascii=False)

def load_settings():
    """アプリ設定を読み込む"""
    if not os.path.exists(SETTINGS_FILE):
        return {"channel_token": "", "user_id": ""}
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {"channel_token": "", "user_id": ""}

def save_settings(settings):
    """アプリ設定を保存する"""
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=4, ensure_ascii=False)

def init_json_db():
    """JSONデータベースファイルを初期化する"""
    if not os.path.exists(DB_FILE):
        save_targets([])
    if not os.path.exists(SETTINGS_FILE):
        save_settings({"channel_token": "", "user_id": ""})

# --- グローバル変数 ---
app_state = {
    "log_history": "",
    "monitoring_active": True
}

# --- ロギング機能の改善 ---
def log_message(message, level="INFO"):
    """タイムスタンプ付きのログメッセージを追加"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"[{timestamp}] [{level}] {message}\n"
    print(log_entry, end='')
    app_state["log_history"] += log_entry
    return log_entry

# --- LINE Messaging APIへの通知機能 ---
def send_message(message):
    """単一のテキストメッセージをLINE Messaging API経由で送信する"""
    settings = load_settings()
    channel_token = settings.get("channel_token", "")
    user_id = settings.get("user_id", "")
    
    if not channel_token or not user_id:
        log_message("LINE設定が完了していません。通知を送信できません。", "ERROR")
        return "LINE設定が完了していません"
    
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

def send_long_message(message):
    """長文メッセージを分割して送信する"""
    max_length = 4800
    full_log = ""

    if len(message) <= max_length:
        log = send_message(message)
        full_log += log + "\n"
    else:
        parts = [message[i:i+max_length] for i in range(0, len(message), max_length)]
        for i, part in enumerate(parts):
            part_header = f"【{i+1}/{len(parts)}】\n"
            log = send_message(part_header + part)
            full_log += log + "\n"
            time.sleep(1)
            
    log_message(f"LINE通知送信完了: {full_log}")
    return full_log

# --- 個別のURLをチェックする関数 ---
def perform_scrape_and_check(target: dict, page: Page):
    """単一のターゲットURLをスクレイピングし、変更をチェックして新しい内容を返す"""
    url = target['url']
    mode = target['mode']
    last_content = target.get('last_content', '')
    notify_on_check = target.get('notify_on_check', False)
    attach_content = target.get('attach_content', False)
    enabled = target.get('enabled', True)

    # 無効なターゲットはスキップ
    if not enabled:
        log_message(f"スキップ (無効): {url}", "DEBUG")
        return None

    log_message(f"チェック開始: {url}", "INFO")
    
    new_content = ""
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=60000)
        time.sleep(10)

        if mode == "エルメスモード (特定要素)":
            elements = page.locator('div.product-item').all_text_contents()
            new_content = "\n".join(line.strip() for el in elements for line in el.strip().split('\n') if line.strip())
        elif mode == "メルカリモード (商品リスト)":
            elements = page.locator('[class*="imageContainer"]').evaluate_all(
                "(elements) => elements.map(e => e.getAttribute('aria-label'))"
            )
            new_content = "\n".join(item.strip() for item in elements if item and item.strip())
        elif mode == "Amazonモード (aria-label)":
            elements = page.locator('h2[class*="a-size-mini a-spacing-none a-color-base s-line-clamp-2"]').evaluate_all(
                "(elements) => elements.map(el => el.getAttribute('aria-label'))"
            )
            new_content = "\n".join(item.strip() for item in elements if item and item.strip())
        elif mode == "楽天モード (a-title)":
            elements = page.locator('a[title]').evaluate_all(
                "(elements) => elements.map(el => el.getAttribute('title'))"
            )
            new_content = "\n".join(item.strip() for item in elements if item and item.strip())
        elif mode == "Yahooショッピングモード (span-content)":
            elements = page.locator('span[class*="SearchResultItemTitle"]').all_text_contents()
            new_content = "\n".join(item.strip() for item in elements if item and item.strip())
        else: # 通常モード
            new_content = page.locator('body').text_content()
            
        site_name = url.split('/')[2]
        
        if target.get('last_content') is None:
            log_message(f"初回コンテンツ取得: {url}", "INFO")
            if notify_on_check:
                message = f"【監視開始】\nサイト「{site_name}」の監視を開始しました。\n{url}"
                if attach_content:
                    content_summary = ""
                    if mode not in ["通常モード (ページ全体)"] and new_content:
                        content_summary = f"\n\n--- 現在のアイテム一覧 ---\n{new_content}"
                    message = f"【監視開始】\nサイト「{site_name}」の監視を開始しました。{content_summary}\n\n{url}"
                send_long_message(message)

        elif last_content != new_content:
            log_message(f"変更を検知: {url}", "WARNING")
            
            message = f"【更新通知】\nサイト「{site_name}」で変化を検知しました！\nすぐに確認してください！\n{url}"
            if attach_content:
                old_lines = last_content.splitlines()
                new_lines = new_content.splitlines()
                diff = difflib.unified_diff(
                    old_lines, new_lines, fromfile='変更前', tofile='変更後', lineterm=''
                )
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
                log_message(f"差分内容: {diff_output}", "DEBUG")
                print(diff_output)
                print(message)
            send_long_message(message)

        else:
            log_message(f"変更なし: {url}", "INFO")
            if notify_on_check:
                message = f"【定期チェック完了】\nサイト「{site_name}」をチェックしました (変更なし)。\n{url}"
                if attach_content:
                    summary_for_no_change = ""
                    if mode not in ["通常モード (ページ全体)"] and new_content:
                        top_items = "\n".join(new_content.split('\n')[:5])
                        summary_for_no_change = f"\n\n--- 最新上位5件 ---\n{top_items}"
                    message = f"【定期チェック完了】\nサイト「{site_name}」をチェックしました (変更なし)。{summary_for_no_change}\n\n{url}"
                send_long_message(message)
        
        return new_content

    except Exception as e:
        log_message(f"エラー発生 ({url}): {e}", "ERROR")
        return None

# --- 永続的な監視ループ ---
def master_monitoring_loop():
    """アプリのバックグラウンドで永続的に実行されるマスターループ"""
    log_message("監視マスタースレッドを開始しました", "INFO")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
             user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        )
        
        try:
            while app_state["monitoring_active"]:
                # チェックが必要なターゲットのリストを取得
                targets_to_check = [
                    t for t in load_targets()
                    if time.time() - t.get('last_checked', 0) > t['interval'] and t.get('enabled', True)
                ]

                if not targets_to_check:
                    log_message("チェック対象なし、5秒待機", "DEBUG")
                    time.sleep(5)
                    continue

                for target in targets_to_check:
                    page = context.new_page()
                    try:
                        new_content = perform_scrape_and_check(target, page)
                        
                        all_targets_now = load_targets()
                        target_to_update = next((t for t in all_targets_now if t['id'] == target['id']), None)

                        if not target_to_update:
                            log_message(f"ID {target['id']} はチェック中に削除されたため、更新をスキップ", "WARNING")
                            continue

                        target_to_update['last_checked'] = time.time()
                        if new_content is not None:
                            target_to_update['last_content'] = new_content
                        
                        save_targets(all_targets_now)

                    except Exception as e:
                        log_message(f"ターゲット処理中のエラー (ID {target.get('id', 'N/A')}): {e}", "ERROR")
                    finally:
                        page.close()

                # 次のチェックサイクルまで待機
                time.sleep(5)
        except Exception as e:
            log_message(f"監視ループで予期せぬエラー: {e}", "ERROR")
        finally:
            browser.close()
            log_message("監視マスタースレッドを終了しました", "INFO")

# --- 監視制御関数 ---
def toggle_monitoring(action):
    """監視を開始/停止する"""
    if action == "stop":
        app_state["monitoring_active"] = False
        log_message("監視を停止しました", "INFO")
        return "監視停止中"
    else:
        if not app_state["monitoring_active"]:
            app_state["monitoring_active"] = True
            # 新しいスレッドで監視を再開
            monitor_thread = threading.Thread(target=master_monitoring_loop, daemon=True)
            monitor_thread.start()
            log_message("監視を再開しました", "INFO")
        return "監視実行中"

def toggle_target_status(target_id, action):
    """特定のターゲットのステータスを変更する"""
    targets = load_targets()
    target = next((t for t in targets if t['id'] == target_id), None)
    
    if not target:
        log_message(f"ID {target_id} が見つかりません", "ERROR")
        return get_targets_as_dataframe()
    
    if action == "delete":
        targets = [t for t in targets if t['id'] != target_id]
        log_message(f"ID {target_id} を削除しました", "INFO")
    elif action == "disable":
        target['enabled'] = False
        log_message(f"ID {target_id} を無効化しました", "INFO")
    elif action == "enable":
        target['enabled'] = True
        log_message(f"ID {target_id} を有効化しました", "INFO")
    
    save_targets(targets)
    return get_targets_as_dataframe()

def save_line_settings(channel_token, user_id):
    """LINE設定を保存する"""
    settings = {"channel_token": channel_token, "user_id": user_id}
    save_settings(settings)
    log_message("LINE設定を保存しました", "INFO")
    return "LINE設定を保存しました"

def test_line_connection():
    """LINE接続テスト"""
    settings = load_settings()
    channel_token = settings.get("channel_token", "")
    user_id = settings.get("user_id", "")
    
    if not channel_token or not user_id:
        return "LINE設定が完了していません"
    
    test_message = "【テスト通知】\nWeb監視ツールからのテスト通知です。\nこのメッセージが表示されれば設定は正常です。"
    
    result = send_message(test_message)
    log_message(f"LINE接続テスト: {result}", "INFO")
    return result

# --- Gradio UIイベントハンドラ ---
def add_target(url, interval, mode, notify_on_check, attach_content):
    """監視対象をJSONに追加する"""
    if not all([url, interval, mode]):
        log_message("必須フィールドを入力してください", "WARNING")
        return get_targets_as_dataframe()
    if not url.startswith('http'):
        log_message("有効なURLを入力してください", "WARNING")
        return get_targets_as_dataframe()

    # LINE設定の確認
    settings = load_settings()
    if not settings.get("channel_token") or not settings.get("user_id"):
        log_message("LINE設定が完了していません。設定タブでLINE設定を完了させてください", "ERROR")
        return get_targets_as_dataframe()

    targets = load_targets()
    
    if any(t['url'] == url for t in targets):
        log_message("このURLは既に追加されています", "WARNING")
        return get_targets_as_dataframe()

    new_id = max([t['id'] for t in targets] + [0]) + 1
    
    new_target = {
        "id": new_id,
        "url": url,
        "mode": mode,
        "interval": int(interval),
        "notify_on_check": notify_on_check,
        "attach_content": attach_content,
        "enabled": True,
        "last_content": None,
        "last_checked": 0
    }
    targets.append(new_target)
    save_targets(targets)
    log_message(f"監視対象を追加しました: {url}", "INFO")
    
    return get_targets_as_dataframe()

def get_targets_as_dataframe():
    """JSONから現在の監視リストを取得し、DataFrameとして返す"""
    targets = load_targets()
    if not targets:
        return pd.DataFrame(columns=['ID', 'URL', 'モード', '間隔(秒)', 'ステータス', '最終チェック日時', '毎回通知', '内容を添付', '操作'])

    df = pd.DataFrame(targets)
    # 古いデータとの互換性
    if 'notify_on_check' not in df.columns:
        df['notify_on_check'] = False
    if 'attach_content' not in df.columns:
        df['attach_content'] = False
    if 'enabled' not in df.columns:
        df['enabled'] = True

    df = df[['id', 'url', 'mode', 'interval', 'enabled', 'last_checked', 'notify_on_check', 'attach_content']]
    df['last_checked'] = df['last_checked'].apply(
        lambda ts: datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S') if ts > 0 else "未チェック"
    )
    df['notify_on_check'] = df['notify_on_check'].apply(lambda x: "はい" if x else "いいえ")
    df['attach_content'] = df['attach_content'].apply(lambda x: "はい" if x else "いいえ")
    df['enabled'] = df['enabled'].apply(lambda x: "有効" if x else "無効")
    
    # 操作ボタンのための列を追加
    df['操作'] = df['id'].apply(lambda x: f"{x}")
    
    df.rename(columns={
        'id':'ID', 'url':'URL', 'mode':'モード', 
        'interval':'間隔(秒)', 'enabled': 'ステータス',
        'last_checked':'最終チェック日時',
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

    with gr.Tab("監視設定"):
        with gr.Row():
            with gr.Column(scale=2):
                # 監視制御ボタン
                with gr.Row():
                    monitor_status = gr.Label(value="監視実行中", label="監視ステータス")
                    stop_btn = gr.Button("監視停止", variant="stop")
                    start_btn = gr.Button("監視開始", variant="primary")
                
                gr.Markdown("### 監視リスト")
                
                # 操作ボタン付きのテーブル表示
                with gr.Row():
                    targets_display = gr.DataFrame(
                        value=get_targets_as_dataframe, 
                        interactive=False,
                        elem_id="targets_table",
                        scale=4
                    )
                    
                    with gr.Column(scale=1):
                        gr.Markdown("### 個別操作")
                        target_id_input = gr.Number(label="操作対象のID", precision=0)
                        with gr.Row():
                            enable_btn = gr.Button("有効化", variant="primary")
                            disable_btn = gr.Button("無効化", variant="secondary")
                        delete_btn = gr.Button("削除", variant="stop")
                        gr.Markdown("※テーブルのIDを確認して入力してください")
                
                refresh_btn = gr.Button("リストを手動更新")
                
                with gr.Accordion("新しい監視対象を追加", open=True):
                    url_input = gr.Textbox(label="監視対象URL", placeholder="https://www.hermes.com/jp/ja/...")
                    with gr.Row():
                        interval_input = gr.Slider(minimum=10, maximum=1800, value=600, step=10, label="監視間隔 (秒)")
                        mode_input = gr.Radio(
                            ["通常モード (ページ全体)", "エルメスモード (特定要素)", "メルカリモード (商品リスト)", "Amazonモード (aria-label)", "楽天モード (a-title)", "Yahooショッピングモード (span-content)"], 
                            label="監視モード", 
                            value="通常モード (ページ全体)"
                        )
                    with gr.Row():
                        notify_on_check_input = gr.Checkbox(label="チェック毎に通知する")
                        attach_content_input = gr.Checkbox(label="通知に内容を添付する", value=True)
                    
                    add_btn = gr.Button("リストに追加", variant="primary")

            with gr.Column(scale=1):
                gr.Markdown("### 実行ログ")
                log_output = gr.Textbox(label=" ", lines=25, interactive=False, autoscroll=True)

    with gr.Tab("LINE設定"):
        gr.Markdown("### LINE通知設定")
        gr.Markdown("ここで設定したLINEアカウントにすべての通知が送信されます。")
        
        settings = load_settings()
        line_token_input = gr.Textbox(
            label="LINE Channel Access Token", 
            value=settings.get("channel_token", ""),
            type="password", 
            placeholder="LINE Developersコンソールから発行した長期トークン"
        )
        line_user_id_input = gr.Textbox(
            label="LINE User ID", 
            value=settings.get("user_id", ""),
            placeholder="あなたのユーザーID (Uから始まる文字列)"
        )
        
        with gr.Row():
            save_line_btn = gr.Button("LINE設定を保存", variant="primary")
            test_line_btn = gr.Button("接続テスト", variant="secondary")
        
        line_status = gr.Textbox(label="ステータス", interactive=False)
        
        # LINE設定のイベントハンドラ
        save_line_btn.click(
            fn=save_line_settings,
            inputs=[line_token_input, line_user_id_input],
            outputs=[line_status]
        )
        test_line_btn.click(
            fn=test_line_connection,
            inputs=[],
            outputs=[line_status]
        )

    # イベントリスナー
    add_btn.click(
        fn=add_target,
        inputs=[url_input, interval_input, mode_input, notify_on_check_input, attach_content_input],
        outputs=[targets_display]
    )
    stop_btn.click(fn=toggle_monitoring, inputs=gr.State("stop"), outputs=[monitor_status])
    start_btn.click(fn=toggle_monitoring, inputs=gr.State("start"), outputs=[monitor_status])
    disable_btn.click(fn=toggle_target_status, inputs=[target_id_input, gr.State("disable")], outputs=[targets_display])
    enable_btn.click(fn=toggle_target_status, inputs=[target_id_input, gr.State("enable")], outputs=[targets_display])
    delete_btn.click(fn=toggle_target_status, inputs=[target_id_input, gr.State("delete")], outputs=[targets_display])
    refresh_btn.click(fn=get_targets_as_dataframe, inputs=None, outputs=[targets_display])
    
    # 定期的な更新処理
    gr.Timer(2).tick(get_logs, None, log_output)
    gr.Timer(10).tick(get_targets_as_dataframe, None, targets_display)

# --- アプリケーションの起動 ---
if __name__ == "__main__":
    print("JSONデータベースを初期化します...")
    init_json_db()
    print("初期化が完了しました。")

    # 初期ログメッセージ
    log_message("アプリを起動しました", "INFO")
    
    # バックグラウンドで監視スレッドを開始
    app_state["monitoring_active"] = True
    monitor_thread = threading.Thread(target=master_monitoring_loop, daemon=True)
    monitor_thread.start()
    
    print("Gradioアプリを起動します...")
    app.launch(server_name="0.0.0.0", server_port=int(os.getenv('PORT', 7860)))