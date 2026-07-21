"""
pdm_reader.py
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from playwright.sync_api import sync_playwright, Page, Frame, TimeoutError as PlaywrightTimeout

PDM_BASE_URL = "https://pap.moxa.com/Windchill"
LOGIN_URL = f"{PDM_BASE_URL}/app/"

DUMMY_PATTERN = re.compile(r"\(Dummy\)", re.IGNORECASE)
NAME_PATTERN = re.compile(r"^([A-Za-z0-9]+_[A-Za-z0-9]+)")

PROJECT2_OID_PATTERN = re.compile(r"Project2(?:%3A|:)(\d+)")


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


class PDMReaderError(Exception):
    pass


PDM_READER_VERSION = "v17-full-oid-prefix"


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


def _find_exact_text(page: Page, text: str):
    candidates: list[Page | Frame] = [page] + list(page.frames)
    for ctx in candidates:
        try:
            loc = ctx.get_by_text(text, exact=True)
            if loc.count() > 0:
                return ctx, loc
        except Exception:
            continue
    return None, None


def debug_print_frames(page: Page) -> None:
    print(f"\n[診斷] 目前頁面共有 {len(page.frames)} 個 frame：")
    for i, f in enumerate(page.frames):
        print(f"  frame[{i}] name={f.name!r} url={f.url}")


def debug_print_inputs(page: Page) -> None:
    candidates: list[Page | Frame] = [page] + list(page.frames)
    total = 0
    for ctx_i, ctx in enumerate(candidates):
        try:
            inputs = ctx.query_selector_all("input")
        except Exception:
            continue
        for inp in inputs:
            total += 1
            try:
                itype = inp.get_attribute("type") or ""
                iname = inp.get_attribute("name") or ""
                iid = inp.get_attribute("id") or ""
                iplaceholder = inp.get_attribute("placeholder") or ""
                ivisible = inp.is_visible()
                print(f"  [ctx={ctx_i}] type={itype!r} name={iname!r} "
                      f"id={iid!r} placeholder={iplaceholder!r} visible={ivisible}")
            except Exception as e:
                print(f"  [ctx={ctx_i}] (讀取屬性失敗: {e})")
    print(f"\n[診斷] 總共找到 {total} 個 <input> 元素")


def debug_print_links(page: Page, keyword: str) -> None:
    candidates: list[Page | Frame] = [page] + list(page.frames)
    total = 0
    for ctx_i, ctx in enumerate(candidates):
        try:
            links = ctx.query_selector_all("a")
        except Exception:
            continue
        for a in links:
            try:
                text = a.inner_text().strip()
            except Exception:
                continue
            if keyword in text:
                total += 1
                try:
                    href = a.get_attribute("href") or ""
                    ivisible = a.is_visible()
                    print(f"  [ctx={ctx_i}] text={text!r} href={href!r} visible={ivisible}")
                except Exception as e:
                    print(f"  [ctx={ctx_i}] text={text!r} (讀取屬性失敗: {e})")
    print(f"\n[診斷] 總共找到 {total} 個包含「{keyword}」的連結")


def debug_print_search_button_candidates(page: Page) -> None:
    candidates: list[Page | Frame] = [page] + list(page.frames)
    for ctx_i, ctx in enumerate(candidates):
        try:
            search_input = ctx.locator("input[name='gloabalSearchField']")
            if search_input.count() == 0:
                continue
        except Exception:
            continue

        print(f"\n[診斷] 在 ctx={ctx_i} 找到搜尋框，往外找可能的搜尋按鈕：")
        container = search_input.locator(
            "xpath=ancestor::td[1] | ancestor::div[1]"
        )
        try:
            container_count = container.count()
        except Exception:
            container_count = 0

        if container_count == 0:
            print("  找不到合理的容器範圍")
            continue

        for level in range(min(container_count, 3)):
            c = container.nth(level)
            try:
                html_snippet = c.inner_html()
            except Exception:
                html_snippet = "(讀取失敗)"
            print(f"  容器[{level}] HTML片段（前800字）：")
            print(f"    {html_snippet[:800]!r}")


def debug_dump_html(page: Page, filepath: str) -> None:
    """診斷用：把當下完整頁面原始碼存成檔案，方便直接上傳分析"""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(page.content())
    print(f"\n[診斷] 已將完整頁面原始碼存至：{filepath}")
    print("[診斷] 請把這個檔案上傳給 Claude 分析。")


@dataclass
class PDMTeamEntry:
    role_code: str
    member_account: str | None
    participated: bool
    raw_text: str


def login(page: Page, username: str, password: str, timeout_ms: int = 20000) -> None:
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


def search_project(page: Page, project_number: str, timeout_ms: int = 40000) -> str:
    ctx, search_input = _find_context(page, "input[name='gloabalSearchField']")
    if search_input is None:
        raise PDMReaderError(
            "找不到搜尋輸入框（已掃描所有frame，name='gloabalSearchField'）。"
        )
    search_input.first.click(force=True)
    search_input.first.press_sequentially(project_number, delay=80)
    page.wait_for_timeout(300)

    search_input.first.press("Enter")
    page.wait_for_timeout(500)

    search_ctx, search_trigger = _find_context(page, "img.global-search-trigger", timeout_ms=3000)
    if search_trigger is not None:
        try:
            search_trigger.first.click(force=True, timeout=3000)
        except Exception:
            try:
                search_trigger.first.evaluate("el => el.click()")
            except Exception:
                pass

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
        raise PDMReaderError(
            f"搜尋後在頁面原始碼中找不到專案 {project_number} 對應的oid，"
            f"已等待{timeout_ms/1000:.0f}秒仍未成功，"
            "可能搜尋未成功觸發，或網路太慢，請確認專案號碼是否正確。"
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
        page = browser.new_page()
        try:
            login(page, username, password)
            oid = search_project(page, project_number)
            open_team_page(page, oid)
            entries = parse_team_table(page)
            return build_role_dict(entries)
        finally:
            browser.close()