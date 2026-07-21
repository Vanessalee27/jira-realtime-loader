"""
JIRA 診斷測試腳本（第二版：改為手動SSO登入）
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright
from src.loaders.jira_reader import (
    goto_ticket_page,
    extract_pdm_project_number,
    extract_assignee_reporter,
    debug_get_field_value_html,
    JIRA_BASE_URL,
)
from src.loaders.jira_debug import (
    debug_print_frames,
    debug_print_inputs,
    debug_dump_html,
    debug_print_field_context,
)

TICKET_ID = "RDS-27773"


def main():
    print("=" * 60)
    print("JIRA 診斷測試開始")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        ticket_url = f"{JIRA_BASE_URL}/browse/{TICKET_ID}"
        print(f"\n[Step 1] 正在前往：{ticket_url}")
        page.goto(ticket_url, timeout=30000)

        input(
            "\n>>> 重要：請在跳出來的瀏覽器視窗裡，完成微軟帳號登入 <<<\n"
            "    （輸入帳密，如果有雙重驗證也請完成）\n"
            "    登入完成、確定看到JIRA票的內容畫面後，"
            "回到這裡按 Enter 繼續..."
        )

        print("\n正在確認頁面是否已穩定...")
        for i in range(10):
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
                break
            except Exception:
                print(f"  頁面似乎還在跳轉，繼續等待...（第{i+1}次）")
                page.wait_for_timeout(2000)
        page.wait_for_timeout(2000)

        print(f"\n[Step 1 確認] 目前網址：{page.url}")
        print(f"目前頁面標題：{page.title()}")

        print(f"\n[Step 2] 開始測試解析欄位")
        try:
            for i in range(5):
                try:
                    pdm_number = extract_pdm_project_number(page)
                    assignee, reporter = extract_assignee_reporter(page)
                    break
                except Exception as e:
                    if "navigating" in str(e).lower():
                        print(f"  頁面仍在跳轉中，重試...（第{i+1}次）")
                        page.wait_for_timeout(2000)
                        continue
                    raise
            else:
                raise Exception("重試5次後頁面仍在跳轉中")

            print(f"M_PDM Project Number: {pdm_number}")
            print(f"Assignee: {assignee}")
            print(f"Reporter: {reporter}")

            if pdm_number is None:
                print("\n[警告] M_PDM Project Number 解析失敗")
                field_html = debug_get_field_value_html(page)
                print(f"[診斷] customfield_11617-val 完整內容：\n{field_html}\n")
                debug_print_field_context(page, "M_PDM Project Number")
                debug_dump_html(page, "jira_field_failure_dump.html")
                print("已存檔 jira_field_failure_dump.html")

            if assignee is None:
                print("\n[警告] Assignee 解析失敗")
                print(f"[診斷] assignee-val 完整內容：\n{debug_get_field_value_html(page, 'assignee-val')}\n")
                debug_print_field_context(page, "Assignee")

            if reporter is None:
                print("\n[警告] Reporter 解析失敗")
                print(f"[診斷] reporter-val 完整內容：\n{debug_get_field_value_html(page, 'reporter-val')}\n")
                debug_print_field_context(page, "Reporter")

        except Exception as e:
            print(f"\n[失敗] 欄位解析出錯：{e}")
            debug_dump_html(page, "jira_field_failure_dump.html")

        input("\n測試完成，按 Enter 關閉瀏覽器...")
        context.close()
        browser.close()


if __name__ == "__main__":
    main()