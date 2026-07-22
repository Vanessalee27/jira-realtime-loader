"""
main.py

JIRA Real-Time Loader Agent —— 正式主流程入口。

完整流程：
  1. 詢問 JIRA ticket 編號
  2. 開瀏覽器登入 JIRA（微軟SSO，需管理員手動完成登入）
     -> 讀出 M_PDM Project Number / Assignee / Reporter
     -> 分別讀出JIRA內建的兩張簽核名單表格：
        「SW Parts承認書簽核名單」與「上傳Source Code簽核名單」
        （只要哪張有資料就處理哪張，不是二選一）
  3. 詢問 PDM 帳密 -> 開另一個瀏覽器登入 PDM（HTTP驗證，自動化）
     -> 依 M_PDM Project Number 查詢專案角色成員（邏輯1）
  4. 對「有資料」的每一張簽核名單，各自執行 resolve_loader()
     跟PDM(邏輯1)比對：
     - 兩邊一致 或 只有一邊有值 -> 自動採用
     - 兩邊不一致 -> 標記衝突，等Reporter從三選項擇一回覆
     - 兩邊皆無 -> 標記待確認
  5. 各自產出獨立的 loader.txt：
     - loader_approval_sheet.txt（承認書簽核名單 vs PDM）
     - loader_source_code.txt（Source Code簽核名單 vs PDM）
  6. 回JIRA ticket留言，內容涵蓋兩份結果：
     - 全部成功 -> @Assignee，附上兩份loader.txt內容
     - 有任一衝突 -> @Reporter，附上三選項（PDM / JIRA / 自行輸入）

已知限制（下一步可以擴充的方向）：
  - SharePoint「文件流簽表」即時比對尚未實作（Step0），目前角色
    代碼對應規則固定寫在 fallback_resolver.py 的 ROLE_MAPPING
  - Reporter用三選項回覆後，目前還沒有「第二階段」自動偵測回覆、
    更新loader.txt、發最終確認留言的流程，需要另外執行一次
  - 尚未整合 Admin Gate 的密碼保護層（verify_password）
  - 未依 RDS_Affairs Request Items 票種判斷該用哪張表，
    目前是「只要有資料的表格都處理」，不做票種篩選

使用方式：
  python main.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright

from src.auth.admin_gate import request_pdm_credentials, AdminGateError
from src.loaders.jira_reader import (
    JIRA_BASE_URL,
    fetch_jira_ticket_info,
    extract_signoff_tables_separately,
)
from src.loaders.pdm_reader import fetch_pdm_team, PDMReaderError
from src.logic.fallback_resolver import resolve_loader, FieldStatus
from src.output.loader_writer import write_loader_txt
from src.jira_notifier import build_initial_comment, post_new_comment

# 兩張簽核名單各自對應的輸出檔名與顯示名稱
SIGNOFF_TABLES = {
    "approval_sheet": "承認書簽核名單",
    "source_code": "Source Code簽核名單",
}


def step_header(step_no: int, total: int, title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"[{step_no}/{total}] {title}")
    print("=" * 60)


def main():
    print("#" * 60)
    print("# JIRA Real-Time Loader Agent")
    print("#" * 60)

    ticket_id = input("\n請輸入 JIRA ticket 編號（例如 RDS-27773）：").strip()
    if not ticket_id:
        print("[錯誤] ticket編號不可為空，程式中止。")
        return

    TOTAL_STEPS = 6

    step_header(1, TOTAL_STEPS, f"開啟 JIRA ticket {ticket_id}，讀取專案資訊")
    with sync_playwright() as p:
        jira_browser = p.chromium.launch(headless=False)
        jira_context = jira_browser.new_context()
        jira_page = jira_context.new_page()

        ticket_url = f"{JIRA_BASE_URL}/browse/{ticket_id}"
        print(f"正在前往：{ticket_url}")
        jira_page.goto(ticket_url, timeout=30000)

        input(
            "\n>>> 請在跳出來的瀏覽器視窗裡完成微軟帳號登入 <<<\n"
            "    登入完成、確定看到JIRA票的內容畫面後，"
            "回到這裡按 Enter 繼續..."
        )

        print("正在確認頁面是否已穩定...")
        for i in range(10):
            try:
                jira_page.wait_for_load_state("networkidle", timeout=5000)
                break
            except Exception:
                jira_page.wait_for_timeout(2000)
        jira_page.wait_for_timeout(2000)

        ticket_info = fetch_jira_ticket_info(jira_page, ticket_id)

        print(f"\nM_PDM Project Number: {ticket_info.pdm_project_number}")
        print(f"Assignee: {ticket_info.assignee_full_name}")
        print(f"Reporter: {ticket_info.reporter_full_name}")

        if ticket_info.pdm_project_number is None:
            print("\n[錯誤] 讀不到 M_PDM Project Number，無法繼續查詢PDM，程式中止。")
            jira_context.close()
            jira_browser.close()
            return

        if ticket_info.assignee_full_name is None or ticket_info.reporter_full_name is None:
            print("\n[警告] Assignee 或 Reporter 讀取失敗，"
                  "留言時的@提及可能會不完整，但不影響主流程繼續。")

        # ------------------------------------------------------------------
        step_header(2, TOTAL_STEPS, "分別讀取JIRA票內建的兩張簽核名單（邏輯2）")
        # ------------------------------------------------------------------
        jira_tables = extract_signoff_tables_separately(jira_page)
        for key, label in SIGNOFF_TABLES.items():
            table_data = jira_tables[key]
            if table_data:
                print(f"[成功] 找到「{label}」，共 {len(table_data)} 個欄位：")
                for k, v in table_data.items():
                    print(f"  {k}: {v}")
            else:
                print(f"[提示] 沒有找到「{label}」，這張表視為查無資料。")

        if not any(jira_tables.values()):
            print("\n[警告] 兩張簽核名單都沒有找到，邏輯2完全查無資料，"
                  "所有欄位將全部改採邏輯1(PDM)結果。")

        # ------------------------------------------------------------------
        step_header(3, TOTAL_STEPS, f"查詢PDM專案 {ticket_info.pdm_project_number} 的成員資料（邏輯1）")
        # ------------------------------------------------------------------
        try:
            username, password = request_pdm_credentials()
        except AdminGateError as e:
            print(f"\n[錯誤] {e}")
            jira_context.close()
            jira_browser.close()
            return

        try:
            pdm_team = fetch_pdm_team(
                username, password, ticket_info.pdm_project_number,
                headless=False, playwright_instance=p,
            )
        except PDMReaderError as e:
            print(f"\n[錯誤] PDM查詢失敗：{e}")
            jira_context.close()
            jira_browser.close()
            return

        print(f"[成功] 取得 {len(pdm_team)} 個角色的成員資料")

        # ------------------------------------------------------------------
        step_header(4, TOTAL_STEPS, "各自執行邏輯1/邏輯2真實比對")
        # ------------------------------------------------------------------
        all_results = {}  # key -> resolved dict
        overall_has_conflict = False

        for key, label in SIGNOFF_TABLES.items():
            jira_table = jira_tables[key]
            if jira_table is None:
                print(f"\n--- {label}：查無此表，跳過比對（不產出對應loader.txt） ---")
                continue

            print(f"\n--- {label} vs PDM 比對結果 ---")
            resolved = resolve_loader(pdm_team, jira_table=jira_table)
            all_results[key] = resolved
            for name, r in resolved.items():
                print(f"  {name}（{r.role_code}）: status={r.status.value} "
                      f"logic1={r.logic1_value} logic2={r.logic2_value} value={r.value}")
                if r.status == FieldStatus.CONFLICT:
                    overall_has_conflict = True
                    print(f"    ⚠️ 發現衝突！PDM={r.logic1_value}  JIRA={r.logic2_value}")

        if not all_results:
            print("\n[錯誤] 兩張簽核名單都查無資料，無法產出任何loader.txt，程式中止。")
            jira_context.close()
            jira_browser.close()
            return

        # ------------------------------------------------------------------
        step_header(5, TOTAL_STEPS, "產出 loader.txt（依表格分別產出）")
        # ------------------------------------------------------------------
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_summaries = {}

        for key, resolved in all_results.items():
            label = SIGNOFF_TABLES[key]
            output_path = os.path.join(script_dir, f"loader_{key}.txt")
            content, summary = write_loader_txt(resolved, output_path=output_path)
            output_summaries[key] = summary

            print(f"\n--- {label} -> {output_path} ---")
            print(content)
            print(f"已解決: {summary['resolved_fields']}")
            print(f"衝突: {summary['conflict_fields']}")
            print(f"待確認: {summary['missing_fields']}")

        # ------------------------------------------------------------------
        step_header(6, TOTAL_STEPS, "回JIRA ticket留言通知")
        # ------------------------------------------------------------------
        assignee = ticket_info.assignee_full_name or "Unknown"
        reporter = ticket_info.reporter_full_name or "Unknown"

        comments_to_send = []
        for key, resolved in all_results.items():
            label = SIGNOFF_TABLES[key]
            comment = build_initial_comment(resolved, assignee, reporter)
            comments_to_send.append((label, comment))

        for label, comment in comments_to_send:
            print(f"\n=== 【{label}】即將送出的留言 ===")
            print("-" * 60)
            print(comment)
            print("-" * 60)

        confirm = input(
            f"\n確定要把上面 {len(comments_to_send)} 則留言都送到JIRA嗎？"
            "(輸入 yes 確認): "
        ).strip().lower()

        if confirm == "yes":
            for label, comment in comments_to_send:
                try:
                    post_new_comment(jira_page, ticket_id, comment)
                    jira_page.wait_for_timeout(2000)
                    print(f"[成功]「{label}」留言已送出。")
                except Exception as e:
                    print(f"[失敗]「{label}」留言送出時發生錯誤：{e}")
        else:
            print("已取消，不會送出任何留言。")

        input("\n全部流程結束，按 Enter 關閉瀏覽器...")
        jira_context.close()
        jira_browser.close()

    print("\n" + "#" * 60)
    print("# 執行完畢")
    print("#" * 60)


if __name__ == "__main__":
    main()