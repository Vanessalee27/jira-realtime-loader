"""
jira_reader.py

負責：
  1. 開啟 JIRA-DC 指定 ticket 頁面（帳密透過瀏覽器context層級帶入）
  2. 解析出 M_PDM Project Number 自訂欄位的值
  3. 解析出 Assignee / Reporter 全名

狀態：尚未用真實環境驗證，這是第二版。已套用開發 pdm_reader.py時
學到的完整經驗：
  - 帳密改用 browser.new_context(http_credentials=...) 帶入
    （PDM那邊證實這是唯一有效的方式，這裡先比照辦理；如果JIRA走的
    是不同的登入機制，這裡會是第一個需要調整的地方）
  - 移除了第一版用猜測的Atlassian帳密表單selector登入方式
    （PDM那邊已經證實「猜表單欄位name」這條路完全走不通）
  - 失敗時自動存檔（debug_dump_html），不用再手動加
  - extract_pdm_project_number() 的比對邏輯已用RDS-27773的靜態
    匯出資料驗證過推理正確（見開發過程紀錄），但尚未用「即時連線
    渲染」的真實頁面驗證過，兩者DOM結構可能不同

TODO(本機驗證)：
  - 確認 JIRA_BASE_URL 是否需要調整（目前用對話中確認過的
    https://jira-dc.moxa.com）
  - 確認帳密是否真的透過http_credentials就能驗證通過，
    如果JIRA走SSO而非HTTP層級驗證，這個方式可能行不通，
    需要另外設計（例如比照PDM最終方案，可能需要先手動排除
    ExtJS特有的雙重觸發bug——但JIRA是Atlassian平台，架構跟
    Windchill完全不同，不代表會有一樣的問題，純粹先保留警覺）
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from playwright.sync_api import Page, Frame
from src.loaders.jira_debug import debug_dump_html

JIRA_BASE_URL = "https://jira-dc.moxa.com"

# 專案號碼格式：目前看過的樣本是2個英文字母+8位數字（如EC24120401），
# 但不確定是否所有格式都符合，保留較寬鬆的比對規則
PROJECT_NUMBER_PATTERN = re.compile(r"\b([A-Z]{1,4}\d{6,10})\b")


class JiraReaderError(Exception):
    pass


@dataclass
class JiraTicketInfo:
    ticket_id: str
    pdm_project_number: str | None
    assignee_full_name: str | None
    reporter_full_name: str | None


def goto_ticket_page(page: Page, ticket_id: str, timeout_ms: int = 30000) -> None:
    """
    導航至指定 JIRA ticket 頁面。帳密驗證透過瀏覽器context層級
    的http_credentials帶入（見 fetch_jira_ticket_info()），這裡
    只需要單純導航過去，並保留緩衝時間因應可能的AJAX渲染延遲。
    """
    issue_url = f"{JIRA_BASE_URL}/browse/{ticket_id}"
    page.goto(issue_url, timeout=timeout_ms)
    page.wait_for_load_state("networkidle", timeout=timeout_ms)

    # 比照PDM的經驗，用真實滑鼠點擊強迫視窗取得系統焦點，
    # 避免背景節流導致頁面資料沒有真正載入完成。
    try:
        page.mouse.click(5, 5)
    except Exception:
        pass
    page.wait_for_timeout(2000)


def extract_pdm_project_number(page: Page) -> str | None:
    """
    從已開啟的 ticket 頁面中，找出「M_PDM Project Number」自訂欄位的值。

    這個欄位有明確ID：customfield_11617（值放在 #customfield_11617-val），
    是JIRA外掛(Riada Insight)的特殊物件型別欄位，值可能透過JavaScript
    非同步載入，因此改用「精準ID + 輪詢等待」的方式。

    如果之後這個ID在其他ticket上不同（自訂欄位ID通常整個JIRA站台是
    固定的，但保留彈性），會退回原本的文字比對方式。
    """
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            value_div = page.locator("#customfield_11617-val")
            if value_div.count() > 0:
                text = value_div.first.inner_text().strip()
                m = PROJECT_NUMBER_PATTERN.search(text)
                if m:
                    return m.group(1)
        except Exception:
            pass
        page.wait_for_timeout(500)

    # 退回機制：用文字比對找標籤，往後找欄位值元素
    label_loc = page.get_by_text("M_PDM Project Number", exact=False)
    try:
        count = label_loc.count()
    except Exception:
        count = 0

    if count > 0:
        try:
            container = label_loc.first.locator(
                "xpath=ancestor::*[self::dt or self::div or self::td][1]"
                "/following-sibling::*[1]"
            )
            if container.count() > 0:
                value_text = container.first.inner_text().strip()
                m = PROJECT_NUMBER_PATTERN.search(value_text)
                if m:
                    return m.group(1)
        except Exception:
            pass

    html = page.content()
    idx = html.find("M_PDM Project Number")
    if idx != -1:
        nearby = html[idx:idx + 500]
        nearby_text = re.sub(r"<[^>]+>", " ", nearby)
        m = PROJECT_NUMBER_PATTERN.search(nearby_text[len("M_PDM Project Number"):])
        if m:
            return m.group(1)

    return None


HEADER_TO_FIELD = {
    "Reviewer1": "REVIEWER",
    "Reviewer2": "REVIEWER2",
    "Reviewer3": "REVIEWER3",
    "Approval": "APPROVER",
    "Mail_Receiver1": "Mail_Reveiver",
}


def extract_signoff_tables_separately(page: Page) -> dict[str, dict[str, str] | None]:
    """
    分別解析「SW Parts承認書簽核名單」與「上傳Source Code簽核名單」
    兩張表格，各自獨立回傳（不是二選一）。

    回傳格式：{'approval_sheet': dict|None, 'source_code': dict|None}
    只要某張表有資料就填入對應的dict，沒有就是None，
    呼叫端應各自跟PDM比對、各自產出一份loader.txt。

    判斷兩張表分別是哪一種：用表頭列的第一格文字（"承認書" 或
    "Source code"）來分類，這格在真實票資料上很穩定乾淨。
    """
    try:
        tables = page.query_selector_all("table")
    except Exception:
        return {"approval_sheet": None, "source_code": None}

    approval_result: dict[str, str] | None = None
    source_code_result: dict[str, str] | None = None

    for table in tables:
        try:
            table_text = table.inner_text()
        except Exception:
            continue
        if "Creater" not in table_text or "簽核者" not in table_text:
            continue

        try:
            rows = table.query_selector_all("tr")
        except Exception:
            continue

        header_row_cells: list[str] | None = None
        name_row_cells: list[str] | None = None

        for row in rows:
            try:
                cells = row.query_selector_all("td, th")
                cell_texts = [c.inner_text().strip() for c in cells]
            except Exception:
                continue
            if not cell_texts:
                continue
            if any(t == "Creater" for t in cell_texts):
                header_row_cells = cell_texts
            elif any("簽核者" in t and ("全名" in t or "姓名" in t) for t in cell_texts):
                name_row_cells = cell_texts

        if header_row_cells is None or name_row_cells is None:
            continue

        result: dict[str, str] = {}
        for i, header_text in enumerate(header_row_cells):
            field_name = HEADER_TO_FIELD.get(header_text)
            if field_name is None:
                continue
            if i >= len(name_row_cells):
                continue
            name_text = name_row_cells[i].strip()
            if name_text:
                result[field_name] = name_text

        if not result:
            continue

        table_label = header_row_cells[0] if header_row_cells else ""
        is_source_code = (
            "source code" in table_label.lower()
            or "source code" in table_text.lower()[:60]
        )

        if is_source_code:
            if source_code_result is None or len(result) > len(source_code_result):
                source_code_result = result
        else:
            if approval_result is None or len(result) > len(approval_result):
                approval_result = result

    return {"approval_sheet": approval_result, "source_code": source_code_result}


def debug_search_field_by_label(page: Page, label_keyword: str, context_chars: int = 1500) -> None:
    """
    診斷用：在頁面原始碼中搜尋指定關鍵字（例如「RDS_SW Parts Info」），
    印出附近的HTML結構，方便確認這個欄位的真實ID與呈現方式
    （可能像M_PDM Project Number一樣是自訂物件欄位，不是一般表格）。
    """
    try:
        html = page.content()
    except Exception as e:
        print(f"[診斷] 讀取頁面原始碼失敗: {e}")
        return

    idx = html.find(label_keyword)
    if idx == -1:
        print(f"[診斷] 頁面原始碼中找不到「{label_keyword}」文字")
        return

    print(f"[診斷]「{label_keyword}」出現位置附近的HTML：")
    print(repr(html[max(0, idx - 200):idx + context_chars]))

    # 順便找找看附近有沒有 customfield_數字 這種ID，方便之後用精準ID抓取
    import re
    nearby = html[max(0, idx - 200):idx + context_chars]
    field_ids = re.findall(r'customfield_\d+', nearby)
    if field_ids:
        print(f"\n[診斷] 附近找到的自訂欄位ID：{set(field_ids)}")


def debug_signoff_table_search(page: Page) -> None:
    """
    診斷用：當 extract_signoff_table() 找不到資料時，印出診斷資訊，
    釐清到底是「這張票真的沒有這個表格」還是「偵測邏輯有bug」。
    """
    try:
        tables = page.query_selector_all("table")
        print(f"[診斷] 頁面上總共有 {len(tables)} 個 <table> 元素")
    except Exception as e:
        print(f"[診斷] 讀取table失敗: {e}")
        tables = []

    try:
        html = page.content()
        has_role_keyword = "簽核角色" in html
        has_signer_keyword = "簽核者" in html
        print(f"[診斷] 頁面原始碼中是否包含「簽核角色」文字: {has_role_keyword}")
        print(f"[診斷] 頁面原始碼中是否包含「簽核者」文字: {has_signer_keyword}")
    except Exception as e:
        print(f"[診斷] 讀取頁面原始碼失敗: {e}")

    for i, table in enumerate(tables):
        try:
            table_text = table.inner_text()
        except Exception:
            continue
        if "簽核" in table_text or "承認書" in table_text:
            print(f"[診斷] 第{i}個table可能相關，前200字：")
            print(f"  {table_text[:200]!r}")

    try:
        debug_dump_html(page, "jira_signoff_search_dump.html")
        print("[診斷] 已將完整頁面原始碼存至 jira_signoff_search_dump.html")
    except Exception:
        pass


def debug_get_field_value_html(page: Page, field_id: str = "customfield_11617-val") -> str:
    """診斷用：直接抓出指定ID欄位的完整HTML內容，方便確認實際渲染結果"""
    try:
        el = page.locator(f"#{field_id}")
        if el.count() > 0:
            return el.first.inner_html()
        return f"(找不到 id={field_id} 的元素)"
    except Exception as e:
        return f"(讀取失敗: {e})"


def extract_assignee_reporter(page: Page) -> tuple[str | None, str | None]:
    """
    從已開啟的 ticket 頁面中，找出 Assignee 與 Reporter 的全名。

    JIRA經典票頁面通常有固定ID：#assignee-val / #reporter-val，
    先用這個精準ID嘗試，抓不到才退回文字標籤比對的方式。
    """
    def _extract_by_id(field_id: str) -> str | None:
        try:
            el = page.locator(f"#{field_id}")
            if el.count() > 0:
                text = el.first.inner_text().strip()
                return text if text else None
        except Exception:
            pass
        return None

    def _extract_by_label(label: str) -> str | None:
        loc = page.get_by_text(label, exact=False)
        try:
            if loc.count() == 0:
                return None
            container = loc.first.locator(
                "xpath=ancestor::*[self::dt or self::div or self::td][1]"
                "/following-sibling::*[1]"
            )
            if container.count() > 0:
                text = container.first.inner_text().strip()
                return text if text else None
        except Exception:
            return None
        return None

    assignee = _extract_by_id("assignee-val") or _extract_by_label("Assignee")
    reporter = _extract_by_id("reporter-val") or _extract_by_label("Reporter")
    return assignee, reporter


def fetch_jira_ticket_info(page: Page, ticket_id: str) -> JiraTicketInfo:
    """
    開啟指定ticket -> 解析出所需資訊。

    重要設計：這個函式假設傳入的 page 已經完成JIRA登入
    （因為JIRA走微軟SSO，不像PDM能用http_credentials帶入帳密，
    需要管理員在瀏覽器視窗手動完成登入，包含可能的雙重驗證）。

    典型用法：程式一開始開一個瀏覽器、讓管理員手動登入一次，
    之後處理多張ticket時重複使用同一個已登入的page，
    不用每張票都重新登入一次。

    如果之後要處理很多張ticket（例如批次處理JIRA上多個Case），
    呼叫端應該只在最開始執行一次登入流程，之後對每張ticket
    重複呼叫這個函式即可。
    """
    goto_ticket_page(page, ticket_id)
    pdm_number = extract_pdm_project_number(page)
    assignee, reporter = extract_assignee_reporter(page)

    if pdm_number is None:
        try:
            debug_dump_html(page, "jira_field_failure_auto_dump.html")
        except Exception:
            pass

    return JiraTicketInfo(
        ticket_id=ticket_id,
        pdm_project_number=pdm_number,
        assignee_full_name=assignee,
        reporter_full_name=reporter,
    )