"""
PDM 網頁 HTML 擷取工具
"""

import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright
from src.loaders.pdm_reader import PDM_BASE_URL
from src.auth.admin_gate import request_pdm_credentials, AdminGateError

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdm_html_captures")


def save_all_frames(page, label: str) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%H%M%S")

    main_path = os.path.join(OUTPUT_DIR, f"{label}_{timestamp}_main.html")
    with open(main_path, "w", encoding="utf-8") as f:
        f.write(page.content())
    print(f"  已存檔：{main_path}")

    for i, frame in enumerate(page.frames):
        if frame == page.main_frame:
            continue
        try:
            frame_content = frame.content()
        except Exception as e:
            print(f"  [frame {i}] 無法讀取內容：{e}")
            continue
        frame_path = os.path.join(OUTPUT_DIR, f"{label}_{timestamp}_frame{i}.html")
        with open(frame_path, "w", encoding="utf-8") as f:
            f.write(frame_content)
        print(f"  已存檔：{frame_path}")


def main():
    print("=" * 60)
    print("PDM 網頁 HTML 擷取工具")
    print("=" * 60)

    print("\n說明：接下來輸入的PDM帳密，只會存在這次執行的電腦記憶體中，")
    print("不會被寫入任何檔案，程式關閉後就自動清除。\n")

    try:
        username, password = request_pdm_credentials()
    except AdminGateError as e:
        print(f"\n[錯誤] {e}")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            http_credentials={"username": username, "password": password}
        )
        page = context.new_page()

        login_url = f"{PDM_BASE_URL}/app/"
        print(f"\n正在前往：{login_url}")
        page.goto(login_url, timeout=20000)

        input(
            "\n>>> 請確認瀏覽器已顯示 PDM 首頁 <<<\n"
            "    確認後按 Enter，程式會存下目前的HTML..."
        )
        print("正在存檔（首頁）...")
        save_all_frames(page, "01_homepage")

        input(
            "\n>>> 接下來請你自己用滑鼠手動搜尋 EC24120401，"
            "直到畫面出現搜尋結果表格 <<<\n"
            "    完成後按 Enter，程式會存下目前的HTML..."
        )
        print("正在存檔（搜尋結果頁）...")
        save_all_frames(page, "02_search_results")

        input(
            "\n>>> 接下來請你自己用滑鼠點擊搜尋結果，進入該專案的詳細頁面 <<<\n"
            "    完成後按 Enter，程式會存下目前的HTML..."
        )
        print("正在存檔（專案詳細頁）...")
        save_all_frames(page, "03_project_detail")

        input(
            "\n>>> 接下來請你自己用滑鼠點擊「小組」，進入小組成員頁面 <<<\n"
            "    完成後按 Enter，程式會存下目前的HTML..."
        )
        print("正在存檔（小組頁面）...")
        save_all_frames(page, "04_team_page")

        print(f"\n全部完成！所有檔案存在這個資料夾：\n  {OUTPUT_DIR}")
        print("請把這個資料夾裡的所有 .html 檔案上傳給 Claude。")

        input("\n按 Enter 關閉瀏覽器...")
        context.close()
        browser.close()


if __name__ == "__main__":
    main()