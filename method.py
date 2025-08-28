import asyncio
from playwright.async_api import async_playwright
import time
async def scrape_web_data(url: str, mode: str):
    """
    指定されたモードに応じて、ウェブページからデータを収集します。
    - 'aria-label': 全てのaria-label属性を取得
    - 'a-title': 全ての<a>タグのtitle属性を取得
    - 'span-content': 特定のclassを持つ<span>のテキストを取得
    """
    
    # 有効なモードを定義
    valid_modes = ['aria-label', 'a-title', 'span-content']
    if mode not in valid_modes:
        print(f"🚨エラー: 無効なモードです。'{', '.join(valid_modes)}'のいずれかを指定してください。")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()

        try:
            print(f"'{url}' にアクセスしています...")
            # ページに移動し、ネットワークが落ち着くまで待機
            await page.goto(url)
            time.sleep(10)
            print(f"モード '{mode}' でデータの収集を開始します。")

            data_list = []
            
            # --- モードに応じて処理を分岐 ---
            
            if mode == 'aria-label':#メルカリ、アマゾン用
                # [aria-label]属性を持つすべての要素からその値を取得
                locator = page.locator('div[aria-label]')
                data_list = await locator.evaluate_all(
                    "(elements) => elements.map(el => el.getAttribute('aria-label'))"
                )
                
            elif mode == 'a-title':#楽天用
                # title属性を持つ全ての<a>タグからその値を取得
                locator = page.locator('a[title]')
                data_list = await locator.evaluate_all(
                    "(elements) => elements.map(el => el.getAttribute('title'))"
                )

            elif mode == 'span-content':#yahoo用
                # classが'SearchResultItemTitle'である<span>タグのテキスト内容を全て取得
                # この場合、all_text_contents()が便利です
                locator = page.locator('span[class*="SearchResultItemTitle"]')
                data_list = await locator.all_text_contents()

            # --- 収集結果の出力 ---

            # 空の要素やNoneを取り除く
            filtered_data = [item.strip() for item in data_list if item and item.strip()]

            if filtered_data:
                print(f"\n✅ {len(filtered_data)}個のデータが見つかりました:")
                for i, item in enumerate(filtered_data, 1):
                    print(f"  {i}: {item}")
            else:
                print("\n❌ 対象のデータは見つかりませんでした。")

        except Exception as e:
            print(f"\n🚨 エラーが発生しました: {e}")
        finally:
            await browser.close()
            print("\nブラウザを閉じました。")

# --- 設定ここから ---

# 1. 調査したいウェブページのURLを指定
target_url = "" 

# 2. 実行したいモードを 'aria-label', 'a-title', 'span-content' から選んで指定
scrape_mode = 'span-content'  # ← ここを書き換えてください

# --- 設定ここまで ---


# 非同期関数を実行
if __name__ == "__main__":
    asyncio.run(scrape_web_data(target_url, scrape_mode))