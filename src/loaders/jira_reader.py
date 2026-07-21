"""
jira_reader.py

負責：
  1. 登入 JIRA-DC（若需要）
  2. 開啟指定 ticket 頁面
  3. 解析出 M_PDM Project Number 自訂欄位的值

狀態：尚未用真實環境驗證，這是第一版。已套用開發 pdm_reader.py
時學到的經驗：
  - 一開始就內建多重比對策略（不是只靠單一selector硬猜）
  - 一開始就有失敗時的診斷/存檔機制（見 jira_debug.py）
  - 保留寬鬆的等待時間，避免因為AJAX/渲染延遲誤判失敗

TODO(本機驗證)：
  - JIRA_BASE_URL 已確認：對話最初你提供過 RDS-27773 票的連結
    「https://jira-dc.moxa.com/browse/RDS-27773」，網址本身沒問題
  - 登入方式未知（帳密表單 / SSO），login() 目前用常見Atlassian
    JIRA欄位名稱猜測，若跟PDM一樣走SSO，需要改成前面用過的
    http_credentials 方式或改用其他驗證方式
  - extract_pdm_project_number() 的比對邏輯是依據先前RDS-27773.doc
    匯出內容（純文字格式「M_PDM Project Number: EC24120401」）推測，
    已用該份真實資料驗證過邏輯正確（見對話紀錄），但那是「靜態匯出檔」
    不是「即時連線渲染的真實頁面」，兩者DOM結構可能不同，
    第一次連線執行很可能還是需要用 jira_debug.py 的工具校正
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from playwright.sync_api import sync_playwright, Page, Frame, TimeoutError as PlaywrightTimeout

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


def login(page: Page, username: str, password: str, timeout_ms: int = 20000) -> None:
    """
    登入 JIRA-DC。

    TODO(本機驗證)：這裡先假設是Atlassian常見的帳密表單登入
    （欄位 name='os_username' / 'os_password'），若實際上走SSO，
    需要改成類似PDM那樣用 browser.new_context(http_credentials=...)
    的方式，或提示管理員手動完成登入。
    """
    page.goto(JIRA_BASE_URL, timeout=timeout_ms)
    try:
        page.wait_for_selector(
            "input[name='os_username'], input#login-form-username",
            timeout=5000,
        )
        page.fill("input[name='os_username'], input#login-form-username", username)
        page.fill("input[name='os_password'], input#login-form-password", password)
        page.click(
            "input[name='login'], button#login-form-submit, button[type='submit']"
        )
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except PlaywrightTimeout:
        raise JiraReaderError(
            "登入頁面元素未找到，可能是 SSO 導向頁面或欄位name不同，"
            "請用 jira_debug.debug_print_inputs() 檢查實際欄位名稱。"
        )


def open_issue(page: Page, ticket_id: str, timeout_ms: int = 30000) -> None:
    """導航至指定 JIRA ticket 頁面"""
    issue_url = f"{JIRA_BASE_URL}/browse/{ticket_id}"
    page.goto(issue_url, timeout=timeout_ms)
    page.wait_for_load_state("networkidle", timeout=timeout_ms)
    page.wait_for_timeout(1500)  # 保留緩衝時間因應AJAX渲染延遲


def extract_pdm_project_number(page: Page) -> str | None:
    """
    從已開啟的 ticket 頁面中，找出「M_PDM Project Number」自訂欄位的值。

    採用多重策略，由嚴謹到寬鬆依序嘗試：
      1. 用 get_by_text 找到標籤文字，往後找緊接著的欄位值元素
      2. 退回用完整頁面原始碼做regex比對（標籤文字後面的第一組
         符合專案號碼格式的文字）
    """
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
        # 先去掉HTML標籤再找符合格式的文字，避免比對到標籤內的雜訊
        nearby_text = re.sub(r"<[^>]+>", " ", nearby)
        m = PROJECT_NUMBER_PATTERN.search(nearby_text[len("M_PDM Project Number"):])
        if m:
            return m.group(1)

    return None


def extract_assignee_reporter(page: Page) -> tuple[str | None, str | None]:
    """
    從已開啟的 ticket 頁面中，找出 Assignee 與 Reporter 的全名。

    TODO(本機驗證)：這兩個欄位的實際HTML結構尚未核對，
    目前先用常見JIRA欄位標籤文字（"Assignee" / "Reporter"）做定位，
    真實環境可能需要調整。
    """
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

    assignee = _extract_by_label("Assignee")
    reporter = _extract_by_label("Reporter")
    return assignee, reporter


def fetch_jira_ticket_info(username: str, password: str, ticket_id: str,
                            headless: bool = True) -> JiraTicketInfo:
    """
    完整流程入口：登入 -> 開啟ticket -> 解析出所需資訊
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            login(page, username, password)
            open_issue(page, ticket_id)
            pdm_number = extract_pdm_project_number(page)
            assignee, reporter = extract_assignee_reporter(page)
            return JiraTicketInfo(
                ticket_id=ticket_id,
                pdm_project_number=pdm_number,
                assignee_full_name=assignee,
                reporter_full_name=reporter,
            )
        finally:
            browser.close()