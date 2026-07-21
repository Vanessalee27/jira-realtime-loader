"""
端到端測試腳本：真實PDM資料 -> 邏輯1/邏輯2比對 -> 產出loader.txt
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.auth.admin_gate import request_pdm_credentials, AdminGateError
from src.loaders.pdm_reader import fetch_pdm_team, PDMReaderError
from src.logic.fallback_resolver import resolve_loader, FieldStatus
from src.output.loader_writer import write_loader_txt

PROJECT_NUMBER = "EC24120401"


def main():
    print("=" * 60)
    print("端到端測試：真實PDM資料 -> loader.txt")
    print("=" * 60)

    try:
        username, password = request_pdm_credentials()
    except AdminGateError as e:
        print(f"\n[錯誤] {e}")
        return

    print(f"\n[1/3] 正在從PDM抓取專案 {PROJECT_NUMBER} 的真實成員資料...")
    print("（這次會跳出瀏覽器視窗，這是目前唯一驗證過穩定能動的設定，"
          "之前改成隱藏視窗模式反而失敗過一次）")
    try:
        pdm_team = fetch_pdm_team(username, password, PROJECT_NUMBER, headless=False)
    except PDMReaderError as e:
        print(f"\n[失敗] PDM抓取失敗：{e}")
        return
    print(f"[成功] 取得 {len(pdm_team)} 個角色的成員資料")

    print("\n[2/3] 執行邏輯1/邏輯2比對...")
    print("（本輪邏輯2傳入None，因為jira_reader.py尚未實作，"
          "會全部落在single_source狀態，之後接上jira_reader.py後行為會改變）")
    resolved = resolve_loader(pdm_team, jira_table=None)

    for name, r in resolved.items():
        print(f"  {name}（{r.role_code}）: status={r.status.value} value={r.value}")

    print("\n[3/3] 產出 loader.txt...")
    content, summary = write_loader_txt(resolved, output_path="loader.txt")

    print("\n=== loader.txt 內容 ===")
    print(content)

    print("=== 摘要 ===")
    print(f"已解決欄位: {summary['resolved_fields']}")
    print(f"衝突欄位: {summary['conflict_fields']}")
    print(f"待確認欄位: {summary['missing_fields']}")

    print("\n[完成] loader.txt 已產出於目前資料夾。")


if __name__ == "__main__":
    main()