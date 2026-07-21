"""
pdm_reader.py

負責：
  1. 使用管理員即時提供的帳密，透過 Playwright 模擬登入 PDM(Windchill)
  2. 依專案號碼搜尋 -> 取得專案 oid -> 進入「小組」頁面
  3. 解析角色/成員表格，回傳 {角色代碼: 成員帳號} dict 供 fallback_resolver 使用
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from playwright.sync_api import sync_playwright, Page, Frame
from src.loaders.pdm_debug import debug_dump_html

PDM_BASE_URL = "https://pap.moxa.com/Windchill"
LOGIN_URL = f"{PDM_BASE_URL}/app/"

PDM_READER_VERSION_NOTE = "v18-enter-only-no-double-search"
DUMMY_PATTERN = re.compile(r"\(Dummy\)", re.IGNORECASE)
NAME_PATTERN = re.compile(r"^([A-Za-z0-9]+_[A-Za-z0-9]+)")

PROJECT2_OID_PATTERN = re.compile(r"Project2(?:%3A|:)(\d+)")


class PDMReaderError(Exception):
    pass


@dataclass
class PDMTeamEntry:
    role_code: str
    member_account: str | None
    participated: bool
    raw_text: str


def _find_context(page: Page, selector: str, timeout_ms: int = 10000):
    deadline = time.time() + timeout_ms / 1000
    while True:
        candidates: list[Page | Frame] = [page] + list(page.frames)
        for ctx in candidates:
            try:
                loc = ctx.locator(selector)
                if loc.count() > 0:
                    return ctx, loc
            except Exception:
                continue
        if time.time() >= deadline:
            return None, None
        page.wait_for_timeout(300)


def _extract_project_oid(html: str, project_number: str) -> str | None:
    pattern = re.compile(
        r'href="[^"]*oid=OR%3Awt\.projmgmt\.admin\.Project2%3A(\d+)[^"]*"[^>]*>([^<]{0,10}'
        + re.escape(project_number) + r'[^<]*)<'
    )
    m = pattern.search(html)
    if m:
        return m.group(1)

    idx = html.find(project_number)
    while idx != -1:
        nearby = html[max(0, idx - 800):idx + 800]
        oid_m = PROJECT2_OID_PATTERN.search(nearby)
        if oid_m:
            return oid_m.group(1)
        idx = html.find(project_number, idx + 1)
    return None


def goto_login_page(page: Page, timeout_ms: int = 20000) -> None:
    page.goto(LOGIN_URL, timeout=timeout_ms)
    page.wait_for_load_state("networkidle", timeout=timeout_ms)

    try:
        page.mouse.click(5, 5)
    except Exception:
        pass
    page.wait_for_timeout(3000)

    try:
        still_on_homepage = page.locator("input[name='gloabalSearchField']").count() > 0
    except Exception:
        still_on_homepage = False

    if not still_on_homepage:
        print("[PDM Reader][WARNING] 偵測到可能誤點跳轉到其他頁面，導回首頁重試...")
        page.goto(LOGIN_URL, timeout=timeout_ms)
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
        page.wait_for_timeout(2000)


def search_project(page: Page, project_number: str, timeout_ms: int = 40000) -> str:
    ctx, search_input = _find_context(page, "input[name='gloabalSearchField']")
    if search_input is None:
        raise PDMReaderError(
            "找不到搜尋輸入框（已掃描所有frame，name='gloabalSearchField'）。"
        )

    page.bring_to_front()
    page.wait_for_timeout(300)

    typed_successfully = False
    for attempt in range(3):
        search_input.first.click(force=True)
        search_input.first.press_sequentially(project_number, delay=100)
        page.wait_for_timeout(500)

        try:
            current_value = search_input.first.input_value()
        except Exception:
            current_value = None

        if current_value == project_number:
            typed_successfully = True
            break

        print(f"[PDM Reader][WARNING] 第{attempt + 1}次打字後驗證失敗"
              f"（目前欄位值={current_value!r}，預期={project_number!r}），"
              "清空後重試...")
        try:
            search_input.first.fill("")
        except Exception:
            pass
        page.wait_for_timeout(300)

    if not typed_successfully:
        raise PDMReaderError(
            f"嘗試3次後，搜尋框仍無法正確輸入「{project_number}」，"
            "這個老舊系統的輸入框可能處於異常狀態，建議重新執行。"
        )

    print(f"[PDM Reader][診斷] 打字驗證成功，欄位值={current_value!r}")

    # 重要：只按Enter，不要再額外點擊放大鏡按鈕！
    # 已用真實診斷資料證實：Enter鍵本身就會正確觸發搜尋
    # （按下後欄位會自動清空回到提示文字「搜尋...」，這是
    # 正常的UX設計，代表搜尋已送出）。如果按完Enter後又追加
    # 點擊放大鏡，這時候欄位已經被清空，等於用空白內容重新
    # 搜尋一次，把原本正確送出的搜尋結果覆蓋掉——這正是之前
    # 版本一直卡住的根本原因。
    page.bring_to_front()
    search_input.first.press("Enter")

    try:
        value_after_enter = search_input.first.input_value()
        print(f"[PDM Reader][診斷] 按Enter後欄位值={value_after_enter!r}"
              "（變成提示文字代表搜尋已正確送出，這是正常現象）")
    except Exception as e:
        print(f"[PDM Reader][診斷] 按Enter後讀取欄位值失敗: {e}")

    deadline = time.time() + timeout_ms / 1000
    oid = None
    poll_count = 0
    while time.time() < deadline:
        html = page.content()
        oid = _extract_project_oid(html, project_number)
        if oid:
            break
        poll_count += 1
        if poll_count % 10 == 0:
            elapsed = int(time.time() - (deadline - timeout_ms / 1000))
            print(f"[PDM Reader] 仍在等待搜尋結果...（已等待約{elapsed}秒）")
        page.wait_for_timeout(500)

    if oid is None:
        try:
            debug_dump_html(page, "pdm_search_failure_auto_dump.html")
        except Exception:
            pass
        raise PDMReaderError(
            f"搜尋後在頁面原始碼中找不到專案 {project_number} 對應的oid，"
            f"已等待{timeout_ms/1000:.0f}秒仍未成功，"
            "可能搜尋未成功觸發，或網路太慢，請確認專案號碼是否正確。"
            "（已自動存檔至 pdm_search_failure_auto_dump.html，可上傳分析）"
        )
    return oid


def open_team_page(page: Page, oid: str, timeout_ms: int = 30000) -> None:
    full_oid = f"OR%3Awt.projmgmt.admin.Project2%3A{oid}"
    team_url = f"{PDM_BASE_URL}/app/#ptc1/project/listTeam?ContainerOid={full_oid}&oid={full_oid}&u8=1"
    page.goto(team_url, timeout=timeout_ms)
    page.wait_for_timeout(500)
    page.reload(timeout=timeout_ms)
    page.wait_for_load_state("networkidle", timeout=timeout_ms)
    page.wait_for_timeout(2000)


def parse_team_table(page: Page) -> list[PDMTeamEntry]:
    html = page.content()
    entries: list[PDMTeamEntry] = []

    for match in re.finditer(r'"teamMemberName":"([^"]+)",', html):
        role_code = match.group(1)

        snippet = html[match.end():match.end() + 3000]
        tooltip_m = re.search(r'"tooltip":"([^"]+)"', snippet)
        participated_m = re.search(r'"joined_proj":\{"gui":\{"html":"([^"]+)"', snippet)

        if tooltip_m is None:
            continue
        member_raw = tooltip_m.group(1)
        participated = (participated_m.group(1) == "是") if participated_m else False

        if DUMMY_PATTERN.search(member_raw):
            entries.append(PDMTeamEntry(
                role_code=role_code, member_account=None,
                participated=participated, raw_text=member_raw,
            ))
            continue

        name_m = NAME_PATTERN.match(member_raw)
        member_account = name_m.group(1) if name_m else None
        entries.append(PDMTeamEntry(
            role_code=role_code, member_account=member_account,
            participated=participated, raw_text=member_raw,
        ))

    return entries


def build_role_dict(entries: list[PDMTeamEntry]) -> dict[str, str]:
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
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            http_credentials={"username": username, "password": password}
        )
        page = context.new_page()
        try:
            goto_login_page(page)
            oid = search_project(page, project_number)
            open_team_page(page, oid)
            entries = parse_team_table(page)
            return build_role_dict(entries)
        finally:
            context.close()
            browser.close()