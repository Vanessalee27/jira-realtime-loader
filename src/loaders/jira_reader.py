"""
jira_reader.py

負責：
  1. 開啟 JIRA-DC 指定 ticket 頁面
  2. 解析出 M_PDM Project Number 自訂欄位的值
  3. 解析出 Assignee / Reporter 全名

狀態：已用真實環境驗證通過（RDS-27773票，三個欄位全部正確擷取）。

技術重點：
  - JIRA走微軟Azure AD SSO登入，不是HTTP驗證，需要管理員在
    瀏覽器視窗手動完成登入（含雙重驗證），無法像PDM一樣用
    http_credentials自動帶入帳密
  - M_PDM Project Number 有固定欄位ID：customfield_11617
    （值在 #customfield_11617-val），是JIRA外掛(Riada Insight)的
    特殊物件型別欄位，值透過JavaScript非同步載入，需要輪詢等待
  - Assignee/Reporter 使用JIRA經典的固定ID：#assignee-val / #reporter-val
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from playwright.sync_api import Page, Frame
from src.loaders.jira_debug import debug_dump_html

JIRA_BASE_URL = "https://jira-dc.moxa.com"

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
    issue_url = f"{JIRA_BASE_URL}/browse/{ticket_id}"
    page.goto(issue_url, timeout=timeout_ms)
    page.wait_for_load_state("networkidle", timeout=timeout_ms)

    try:
        page.mouse.click(5, 5)
    except Exception:
        pass
    page.wait_for_timeout(2000)


def extract_pdm_project_number(page: Page) -> str | None:
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


def debug_get_field_value_html(page: Page, field_id: str = "customfield_11617-val") -> str:
    try:
        el = page.locator(f"#{field_id}")
        if el.count() > 0:
            return el.first.inner_html()
        return f"(找不到 id={field_id} 的元素)"
    except Exception as e:
        return f"(讀取失敗: {e})"


def extract_assignee_reporter(page: Page) -> tuple[str | None, str | None]:
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
    （因為JIRA走微軟SSO，需要管理員在瀏覽器視窗手動完成登入，
    含可能的雙重驗證）。

    典型用法：程式一開始開一個瀏覽器、讓管理員手動登入一次，
    之後處理多張ticket時重複使用同一個已登入的page，
    不用每張票都重新登入一次。
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