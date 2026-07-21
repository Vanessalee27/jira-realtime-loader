"""
JIRA 診斷測試腳本

用途：測試 jira_reader.py 能不能正確登入JIRA-DC、開啟ticket、
解析出 M_PDM Project Number / Assignee / Reporter。

使用方式：
  python test_jira_login.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright
from src.loaders.jira_reader import (
    login,
    open_issue,
    extract_pdm_project_number,
    extract_assignee_reporter,
    JIRA_BASE_URL,
    JiraReaderError,
)
from src.loaders.jira_debug import (
    debug_print_frames,
    debug_print_inputs,
    debug_dump_html,
    debug_print_field_context,
)
from src.auth.admin_gate import AdminGateError
import getpass

TICKET_ID = "RDS-27773"


def request_jira_credentials():
    """
    暫時獨立寫一份JIRA帳密請求（跟PDM的分開，因為兩個系統帳密
    很可能不同）。之後若確認共用同一組公司帳密，可以合併簡化。
    """
    print("[Admin Gate] 需要 JIRA 存取權限，請管理者輸入帳密。")
    username = input("JIRA 帳號：").strip()
    password = getpass.getpass("JIRA 密碼：")
    if not username or not password:
        raise AdminGateError("JIRA 帳密不可為空，系統中止。")
    return username, password


def main():
    print("=" * 60)
    print("JIRA 診斷測試開始")
    print("=" * 60)

    print("\n說明：接下來輸入的JIRA帳密，只會存在這次執行的電腦記憶體中，")
    print("不會被寫入任何檔案，程式關閉後就自動清除。\n")

    try:
        username, password = request_jira_credentials()
    except AdminGateError as e:
        print(f"\n[錯誤] {e}")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            http_credentials={"username": username, "password": password}
        )
        page = context.new_page()

        print(f"\n[Step 1] 正在前往：{JIRA_BASE_URL}")
        try:
            page.goto(JIRA_BASE_URL, timeout=20000)
        except Exception as e:
            print(f"\n[失敗] 無法開啟JIRA首頁：{e}")
            context.close()
            browser.close()
            return

        input(
            "\n>>> 請確認瀏覽器視窗是否已顯示 JIRA 首頁（或已自動登入）<<<\n"
            "    如果卡在登入畫面，請自己手動完成登入。\n"
            "    確認後按 Enter 繼續..."
        )

        print(f"\n[Step 2] 開始測試開啟 ticket：{TICKET_ID}")
        try:
            open_issue(page, TICKET_ID)
            print(f"[成功] 已開啟，目前網址：{page.url}")
            print(f"目前頁面標題：{page.title()}")
        except Exception as e:
            print(f"\n[失敗] 開啟ticket出錯：{e}")
            debug_print_frames(page)
            debug_dump_html(page, "jira_open_failure_dump.html")
            input("\n完成後按 Enter 結束測試...")
            context.close()
            browser.close()
            return

        print(f"\n[Step 3] 開始測試解析欄位")
        try:
            pdm_number = extract_pdm_project_number(page)
            assignee, reporter = extract_assignee_reporter(page)

            print(f"M_PDM Project Number: {pdm_number}")
            print(f"Assignee: {assignee}")
            print(f"Reporter: {reporter}")

            if pdm_number is None:
                print("\n[警告] M_PDM Project Number 解析失敗，")
                print("印出附近HTML結構供診斷：")
                debug_print_field_context(page, "M_PDM Project Number")
                debug_dump_html(page, "jira_field_failure_dump.html")

        except Exception as e:
            print(f"\n[失敗] 欄位解析出錯：{e}")
            debug_dump_html(page, "jira_field_failure_dump.html")

        input("\n測試完成，按 Enter 關閉瀏覽器...")
        context.close()
        browser.close()


if __name__ == "__main__":
    main()