"""
JIRA 留言測試腳本（診斷加強版）

這次不直接呼叫 post_new_comment()，改成逐步執行 + 逐步印出診斷資訊，
確認每一步到底有沒有真的生效，而不是等到最後才發現整體沒成功。
"""

import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright
from src.loaders.jira_reader import JIRA_BASE_URL
from src.loaders.jira_debug import debug_dump_html, debug_print_frames

TICKET_ID = "RDS-27773"

TEST_COMMENT = (
    f"[AI工具測試留言，可以刪除] 診斷測試 "
    f"時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
)


def main():
    print("=" * 60)
    print("JIRA 留言診斷測試開始")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        ticket_url = f"{JIRA_BASE_URL}/browse/{TICKET_ID}"
        print(f"\n[Step 1] 正在前往：{ticket_url}")
        page.goto(ticket_url, timeout=30000)

        input(
            "\n>>> 請在跳出來的瀏覽器視窗裡完成微軟帳號登入 <<<\n"
            "    確認後按 Enter 繼續..."
        )

        for i in range(10):
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
                break
            except Exception:
                page.wait_for_timeout(2000)
        page.wait_for_timeout(2000)

        print(f"\n即將測試留言，內容：{TEST_COMMENT}")
        confirm = input("確定要繼續嗎？(輸入 yes 確認): ").strip().lower()
        if confirm != "yes":
            print("已取消。")
            context.close()
            browser.close()
            return

        print("\n[診斷1] 點擊留言按鈕...")
        try:
            page.click("a#footer-comment-button", timeout=10000)
            print("  點擊成功，沒有丟出例外")
        except Exception as e:
            print(f"  點擊失敗: {e}")

        page.wait_for_timeout(1500)

        print("\n[診斷2] 檢查TinyMCE編輯器iframe...")
        try:
            page.wait_for_selector("iframe[id^='mce_'][id$='_ifr']", timeout=10000)
            print("  找到編輯器iframe")
        except Exception as e:
            print(f"  找不到編輯器iframe: {e}")
            debug_print_frames(page)
            debug_dump_html(page, "jira_comment_diag_dump.html")
            input("按 Enter 結束...")
            context.close()
            browser.close()
            return

        editor_frame = page.frame_locator("iframe[id^='mce_'][id$='_ifr']")
        editor_body = editor_frame.locator("body")

        print("\n[診斷3] 填入留言內容（改用TinyMCE API方式）...")
        try:
            page.evaluate(
                """(content) => {
                    if (window.tinymce && window.tinymce.activeEditor) {
                        window.tinymce.activeEditor.setContent(content);
                        window.tinymce.activeEditor.fire('change');
                        window.tinymce.activeEditor.fire('keyup');
                    }
                }""",
                TEST_COMMENT,
            )
            print("  TinyMCE API 呼叫完畢，沒有丟出例外")
        except Exception as e:
            print(f"  TinyMCE API 呼叫失敗: {e}")

        page.wait_for_timeout(1000)

        print("\n[診斷4] 讀回編輯器內容，確認真的填進去了...")
        try:
            actual_text = editor_body.inner_text()
            print(f"  編輯器目前內容：{actual_text!r}")
            print(f"  是否符合預期：{TEST_COMMENT in actual_text}")
        except Exception as e:
            print(f"  讀取失敗: {e}")

        print("\n[診斷5] 找送出按鈕，列出所有候選、可見性、啟用狀態...")
        submit_candidates = [
            "#issue-comment-add-submit",
            "button:has-text('新增')",
            "button:has-text('儲存')",
            "button:has-text('Save')",
            "input[type='submit']",
            "button:has-text('Add')",
        ]
        chosen_selector = None
        for sel in submit_candidates:
            try:
                loc = page.locator(sel)
                count = loc.count()
                if count > 0:
                    visible = loc.first.is_visible()
                    enabled = loc.first.is_enabled()
                    print(f"  selector={sel!r} 找到{count}個，"
                          f"第一個visible={visible} enabled={enabled}")
                    if visible and enabled and chosen_selector is None:
                        chosen_selector = sel
                else:
                    print(f"  selector={sel!r} 找到0個")
            except Exception as e:
                print(f"  selector={sel!r} 檢查失敗: {e}")

        if chosen_selector is None:
            print("\n  第一輪沒有找到enabled的按鈕，等待5秒後重新檢查一次...")
            page.wait_for_timeout(5000)
            for sel in submit_candidates:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible() and loc.first.is_enabled():
                    chosen_selector = sel
                    print(f"  重新檢查後找到可用按鈕：{sel}")
                    break

        if chosen_selector:
            print(f"\n[診斷6] 點擊送出按鈕：{chosen_selector}")
            try:
                page.locator(chosen_selector).first.click(timeout=10000)
                print("  點擊成功，沒有丟出例外")
            except Exception as e:
                print(f"  點擊失敗: {e}")
        else:
            print("\n[診斷6] 沒有找到可見的送出按鈕，跳過點擊")

        page.wait_for_timeout(3000)

        print(f"\n[Step 最終] 目前頁面網址：{page.url}")
        debug_dump_html(page, "jira_comment_diag_dump.html")
        print("已存檔 jira_comment_diag_dump.html")

        input(
            "\n>>> 請直接用肉眼確認瀏覽器畫面上，留言區有沒有出現剛剛的測試留言 <<<\n"
            "    確認後按 Enter 結束測試..."
        )

        context.close()
        browser.close()


if __name__ == "__main__":
    main()