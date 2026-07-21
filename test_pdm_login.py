"""
PDM 診斷測試腳本
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright
from src.loaders.pdm_reader import (
    search_project,
    open_team_page,
    parse_team_table,
    build_role_dict,
    debug_print_frames,
    debug_print_inputs,
    debug_print_links,
    debug_dump_html,
    PDM_BASE_URL,
    PDM_READER_VERSION,
    PDMReaderError,
)
from src.auth.admin_gate import request_pdm_credentials, AdminGateError

PROJECT_NUMBER = "EC24120401"


def main():
    print("=" * 60)
    print("PDM 診斷測試開始")
    print(f"pdm_reader.py 版本：{PDM_READER_VERSION}")
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
        print(f"\n[Step 1] 正在前往：{login_url}")

        try:
            page.goto(login_url, timeout=20000)
        except Exception as e:
            print(f"\n[失敗] 無法開啟登入頁：{e}")
            print("可能原因：帳密錯誤，或網址/驗證方式與預期不同。")
            context.close()
            browser.close()
            return

        input(
            "\n>>> 請確認瀏覽器視窗是否已顯示 PDM 首頁 <<<\n"
            "    確認後按 Enter 繼續..."
        )

        print(f"\n[Step 2] 開始測試搜尋專案：{PROJECT_NUMBER}")
        try:
            oid = search_project(page, PROJECT_NUMBER)
            print(f"[成功] 取得專案 oid = {oid}")
        except PDMReaderError as e:
            print(f"\n[失敗] 搜尋步驟出錯：{e}")
            print(f"目前頁面網址：{page.url}")
            print(f"目前頁面標題：{page.title()}")
            debug_print_frames(page)
            debug_print_links(page, PROJECT_NUMBER)
            debug_dump_html(page, "search_failure_dump.html")
            input("\n完成後按 Enter 結束測試...")
            context.close()
            browser.close()
            return

        print(f"\n[Step 3] 開始測試導航至小組頁面並解析角色/成員")
        try:
            open_team_page(page, oid)
            entries = parse_team_table(page)

            if not entries:
                print("[警告] 解析結果是空的，可能是表格 selector 需要調整。")
                debug_dump_html(page, "team_page_empty_dump.html")
            else:
                print(f"[成功] 解析出 {len(entries)} 筆資料：\n")
                for e in entries:
                    print(f"  角色={e.role_code:15s} "
                          f"成員={str(e.member_account):15s} "
                          f"已參與={e.participated}")

                role_dict = build_role_dict(entries)
                print("\n=== 邏輯1需要的5個角色代碼比對 ===")
                needed = ["SW RQM", "PE NP", "SW STM", "SW PJM UM", "PE NP UM"]
                for r in needed:
                    print(f"  {r}: {role_dict.get(r, '❌ 查無')}")

        except Exception as e:
            print(f"\n[失敗] 小組頁面測試出錯：{e}")
            print(f"目前頁面網址：{page.url}")
            print("請截圖目前畫面，以及按F12看到的表格HTML結構給我。")

        input("\n測試完成，按 Enter 關閉瀏覽器...")
        context.close()
        browser.close()


if __name__ == "__main__":
    main()