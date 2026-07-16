"""
pdm_reader.py

負責：
  1. 使用管理員即時提供的帳密，透過 Playwright 模擬登入 PDM(Windchill)
  2. 依專案號碼搜尋 -> 取得專案 oid -> 進入「小組」頁面
  3. 解析角色/成員表格，回傳 {角色代碼: 成員帳號} dict 供 fallback_resolver 使用

重要提醒：
  本檔案的登入/搜尋 CSS selector 是依據使用者提供的頁面截圖與網址結構推導，
  Windchill 是舊式 Java/GWT 應用，實際 DOM 結構務必在正式環境用瀏覽器
  開發者工具(F12)核對後調整，標記 TODO 的地方尤其需要在本機環境驗證。

  parse_team_table() 的表格解析邏輯已用截圖真實文字內容做過模擬驗證
  （見 tests/test_pdm_reader.py），可信度較高；登入與搜尋流程則尚未
  實測，需要你在本機用真實帳密跑過一次才能確認 selector 是否正確。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeout

PDM_BASE_URL = "https://pap.moxa.com/Windchill"
LOGIN_URL = f"{PDM_BASE_URL}/app/"

DUMMY_PATTERN = re.compile(r"\(Dummy\)", re.IGNORECASE)
# 匹配 'EddieYC_Chen(陳裕佳)' 開頭的帳號部分，帳號格式為 normalize_name() 的輸出格式
NAME_PATTERN = re.compile(r"^([A-Za-z0-9]+_[A-Za-z0-9]+)")


class PDMReaderError(Exception):
    pass


@dataclass
class PDMTeamEntry:
    role_code: str
    member_account: str | None   # None 代表 Dummy 佔位或未指派
    participated: bool
    raw_text: str


def login(page: Page, username: str, password: str, timeout_ms: int = 20000) -> None:
    """
    登入 PDM(Windchill)。

    TODO(本機驗證)：若公司走 SSO（非帳密表單登入），這裡需改成偵測轉導頁面，
    並提示管理員手動完成 SSO 步驟後再繼續（比照 Admin Gate 的人工介入原則），
    而不是硬要用 selector 去填帳密表單。
    """
    page.goto(LOGIN_URL, timeout=timeout_ms)
    try:
        page.wait_for_selector("input[name='j_username'], #username", timeout=5000)
        page.fill("input[name='j_username'], #username", username)
        page.fill("input[name='j_password'], #password", password)
        page.click("button[type='submit'], input[type='submit']")
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except PlaywrightTimeout:
        raise PDMReaderError(
            "登入頁面元素未找到，可能是 SSO 導向頁面，"
            "請確認是否需要改為手動介入登入流程。"
        )


def search_project(page: Page, project_number: str, timeout_ms: int = 15000) -> str:
    """
    在「搜尋(S)」頁籤搜尋專案號碼，點擊完全符合的結果連結，
    回傳導航後頁面 URL 中解析出的 oid（供組 team 頁面網址使用）。
    """
    page.click("text=搜尋(S)")
    page.wait_for_selector("input[type='search'], input[name='searchKeyword']", timeout=timeout_ms)
    page.fill("input[type='search'], input[name='searchKeyword']", project_number)
    page.keyboard.press("Enter")
    page.wait_for_load_state("networkidle", timeout=timeout_ms)

    link = page.locator(f"a:text-is('{project_number}')")
    if link.count() == 0:
        raise PDMReaderError(f"搜尋結果中找不到專案號碼 {project_number}，請確認號碼是否正確。")
    link.first.click()
    page.wait_for_load_state("networkidle", timeout=timeout_ms)

    current_url = page.url
    match = re.search(r"oid=([\w%:.]+)", current_url)
    if not match:
        raise PDMReaderError(f"無法從網址解析出 oid：{current_url}")
    return match.group(1)


def navigate_to_team_page(page: Page, oid: str, timeout_ms: int = 15000) -> None:
    """導航至專案的「小組」頁面"""
    team_url = f"{PDM_BASE_URL}/app/#ptc1/project/listTeam?ContainerOid={oid}&oid={oid}&u8=1"
    page.goto(team_url, timeout=timeout_ms)
    page.wait_for_selector("table", timeout=timeout_ms)
    page.wait_for_timeout(1500)  # Windchill 畫面常有渲染延遲


def parse_team_table(page: Page) -> list[PDMTeamEntry]:
    """
    解析角色/成員表格。

    Windchill 小組頁面結構為樹狀表格：
      - 父列：角色代碼（如 'SW RQM'），有展開/摺疊圖示，本身無成員資訊
      - 子列（縮排）：實際成員，文字格式為 'EddieYC_Chen(陳裕佳)'
                       或佔位格式 '角色代碼 (Dummy)'
      - 「已參與」欄位為 是/否，不代表角色是否有效指派，僅代表該成員
        是否已於系統中確認參與，解析時不以此欄位過濾資料。
    """
    rows = page.query_selector_all("table tr")

    entries: list[PDMTeamEntry] = []
    current_role_code: str | None = None

    for row in rows:
        cells = row.query_selector_all("td")
        if not cells:
            continue

        row_text = row.inner_text().strip()
        if not row_text:
            continue

        is_parent_row = row.query_selector(
            "img[title*='摺疊'], img[title*='展開'], img[alt*='摺疊'], img[alt*='展開']"
        ) is not None

        if is_parent_row:
            role_cell = cells[0].inner_text().strip()
            current_role_code = role_cell.split("\n")[0].strip()
            continue

        if current_role_code is None:
            continue

        member_cell_text = cells[0].inner_text().strip() if cells else row_text

        participated_text = ""
        for c in cells:
            t = c.inner_text().strip()
            if t in ("是", "否"):
                participated_text = t
                break
        participated = participated_text == "是"

        if DUMMY_PATTERN.search(member_cell_text):
            entries.append(PDMTeamEntry(
                role_code=current_role_code, member_account=None,
                participated=participated, raw_text=member_cell_text,
            ))
            continue

        match = NAME_PATTERN.match(member_cell_text)
        member_account = match.group(1) if match else None
        entries.append(PDMTeamEntry(
            role_code=current_role_code, member_account=member_account,
            participated=participated, raw_text=member_cell_text,
        ))

    return entries


def build_role_dict(entries: list[PDMTeamEntry]) -> dict[str, str]:
    """
    將解析結果轉為 {角色代碼: 成員帳號} dict，供 fallback_resolver 直接使用。
    Dummy / 未指派角色不會出現在回傳的 dict 中（視同 resolve_field 判斷查無資料）。
    """
    result: dict[str, str] = {}
    for e in entries:
        if e.member_account is None:
            continue
        if e.role_code in result:
            print(f"[PDM Reader][WARNING] 角色 {e.role_code} 有多筆成員，"
                  f"已採用第一筆 '{result[e.role_code]}'，忽略 '{e.member_account}'")
            continue
        result[e.role_code] = e.member_account
    return result


def fetch_pdm_team(username: str, password: str, project_number: str,
                    headless: bool = True) -> dict[str, str]:
    """
    完整流程入口：登入 -> 搜尋專案 -> 進入小組頁 -> 解析 -> 回傳角色/成員 dict

    帳密由呼叫端透過 admin_gate.request_pdm_credentials() 即時取得，
    此函式不主動要求輸入，也不落地儲存帳密，函式結束後帳密變數
    由呼叫端負責清除（session.pdm_username = session.pdm_password = None）。
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            login(page, username, password)
            oid = search_project(page, project_number)
            navigate_to_team_page(page, oid)
            entries = parse_team_table(page)
            return build_role_dict(entries)
        finally:
            browser.close()
