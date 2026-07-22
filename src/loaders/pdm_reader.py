"""
pdm_reader.py

負責：
  1. 使用管理員即時提供的帳密，透過 Playwright 模擬登入 PDM(Windchill)
  2. 依專案號碼搜尋 -> 取得專案 oid -> 進入「小組」頁面
  3. 解析角色/成員表格，回傳 {角色代碼: 成員帳號} dict 供 fallback_resolver 使用

狀態：已用真實PDM環境完整驗證通過（EC24120401專案，5個目標角色
      SW RQM/PE NP/SW STM/SW PJM UM/PE NP UM 全部正確解析）。

技術重點（開發過程中確認的關鍵細節，修改時務必留意）：
  - 搜尋框 name='gloabalSearchField'（注意這是網站本身的拼字錯誤
    "gloabal" 而非 "global"）
  - 搜尋框必須用「模擬真人逐字打字」(press_sequentially) 而非直接
    .fill()，否則這個舊版ExtJS框架偵測不到輸入事件
  - 搜尋觸發後不論畫面是否有可見的導航反應，一律直接輪詢頁面原始碼
    找出目標專案的oid，比依賴DOM點擊可靠得多
  - oid 在頁面上有兩種編碼格式（href網址編碼 %3A / 樹狀選單屬性純冒號 :），
    擷取時需同時支援
  - 小組頁面網址需要完整物件類型前綴
    「OR%3Awt.projmgmt.admin.Project2%3A」，只給純數字oid會出現
    「Not a ContainerTeamManaged Object」錯誤
  - 導航到小組頁面後需強制 reload()，因為這是hash路由的SPA，
    單純改網址hash不會觸發完整的換頁/抓資料邏輯
  - 小組成員資料改用JSON字串直接擷取（頁面背後由JSON驅動渲染），
    不解析DOM表格結構，準確度更高、更不受版面變化影響

除錯需求：如果之後這裡的 selector 又失效（例如系統改版），
可以匯入 pdm_debug.py 裡的診斷工具（debug_print_frames /
debug_print_inputs / debug_print_links / debug_dump_html）
輔助排查，不需要動到這支正式程式碼。
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
# 匹配 'EddieYC_Chen(陳裕佳)' 開頭的帳號部分，帳號格式為 normalize_name() 的輸出格式
NAME_PATTERN = re.compile(r"^([A-Za-z0-9]+_[A-Za-z0-9]+)")

PROJECT2_OID_PATTERN = re.compile(r"Project2(?:%3A|:)(\d+)")


class PDMReaderError(Exception):
    pass


@dataclass
class PDMTeamEntry:
    role_code: str
    member_account: str | None   # None 代表 Dummy 佔位或未指派
    participated: bool
    raw_text: str


def _find_context(page: Page, selector: str, timeout_ms: int = 10000):
    """
    在頁面本身及所有 frame 中尋找 selector，回傳第一個找到可見符合結果的
    (frame_or_page, locator)。持續輪詢重試直到逾時，避免AJAX渲染還沒
    完成就誤判找不到。
    """
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
    """
    直接在頁面原始碼裡找出目標專案的 oid，不透過DOM點擊導航。

    這個系統不同地方用不同的oid編碼格式：
      - 一般連結：href裡用網址編碼 Project2%3A1673797403
      - ExtJS樹狀選單：自訂屬性 ext:tree-node-id 裡用純冒號 Project2:1673797403
    這裡同時支援兩種格式，並用較大的搜尋範圍（800字元）確保不會因為
    oid跟專案號碼文字之間距離較遠而找不到。
    """
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
    """
    前往PDM首頁。這個系統走的是HTTP層級驗證（非網頁帳密表單），
    帳密驗證是在建立瀏覽器context時透過http_credentials帶入
    （見 fetch_pdm_team()），這裡導航過去後，額外等待頁面完全
    載入+緩衝時間，確保這個老舊系統的JavaScript框架（ExtJS）
    有足夠時間完成初始化。

    重要修正：用真實滑鼠點擊（page.mouse.click）強迫瀏覽器視窗
    取得「作業系統層級」的真實焦點，不只是Playwright/Chromium
    內部認定的焦點。從終端機啟動的自動化流程，焦點預設可能還留在
    終端機視窗上，導致Chrome判定瀏覽器視窗為「背景視窗」，
    對其JavaScript執行做節流（省電機制），使得這個依賴JS動態
    渲染的老舊系統資料卡在初始狀態不會更新（已觀察到現象：
    無論等待多久，畫面停留在首頁「共114個物件」的狀態不變）。
    """
    page.goto(LOGIN_URL, timeout=timeout_ms)
    page.wait_for_load_state("networkidle", timeout=timeout_ms)

    # 用真實滑鼠點擊頁面空白處，強迫視窗真正取得系統焦點。
    # 座標選在頁面最上方logo/標題列附近（通常是空白區域），
    # 避免點到主要內容區的資料列（曾經誤點到任務清單裡的連結，
    # 導致意外跳轉到不相關的頁面）。
    try:
        page.mouse.click(5, 5)
    except Exception:
        pass
    page.wait_for_timeout(3000)

    # 安全檢查：萬一不小心點到連結導致跳轉到別的頁面
    # （例如任務清單裡的項目），偵測搜尋框是否還存在，
    # 不存在就導回首頁重新開始。
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
    """
    觸發搜尋（打字 + 按Enter + 點放大鏡，雙管齊下增加觸發AJAX的機會），
    接著不管畫面上是否有肉眼可見的導航反應，直接輪詢頁面原始碼，
    掃描找出目標專案的oid。

    timeout_ms 預設40秒：這個老系統的AJAX搜尋回應偶爾會超過15秒才
    完成（尤其網路較慢時），太短的逾時會誤判失敗。

    回傳專案oid字串（例如 '1673797403'）。
    """
    ctx, search_input = _find_context(page, "input[name='gloabalSearchField']")
    if search_input is None:
        raise PDMReaderError(
            "找不到搜尋輸入框（已掃描所有frame，name='gloabalSearchField'）。"
        )

    # 強制把瀏覽器視窗帶到最前面、真正取得作業系統層級焦點，
    # 這個老舊系統的部分內部邏輯可能依賴真實視窗焦點狀態，
    # 光是DOM層級的value正確不代表框架內部真的認可這次輸入。
    page.bring_to_front()
    page.wait_for_timeout(300)

    # 打字後驗證是否真的生效（這個老舊ExtJS框架偶爾會有打字沒被
    # 正確接收的狀況），沒成功就重打，最多嘗試3次
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
    """
    直接組出小組頁面網址並導航過去，接著強制重新整理。

    oid 參數只是純數字（例如'1673797403'），組網址時必須補上完整
    的物件類型前綴「OR%3Awt.projmgmt.admin.Project2%3A」，系統才
    知道這是一個專案物件。

    這是hash路由的單頁應用程式(SPA)，導航後強制reload，確保SPA
    從頭初始化該頁面對應的元件與資料。
    """
    full_oid = f"OR%3Awt.projmgmt.admin.Project2%3A{oid}"
    team_url = f"{PDM_BASE_URL}/app/#ptc1/project/listTeam?ContainerOid={full_oid}&oid={full_oid}&u8=1"
    page.goto(team_url, timeout=timeout_ms)
    page.wait_for_timeout(500)
    page.reload(timeout=timeout_ms)
    page.wait_for_load_state("networkidle", timeout=timeout_ms)
    page.wait_for_timeout(2000)  # Windchill 畫面常有渲染延遲，reload後多等一些


def parse_team_table(page: Page) -> list[PDMTeamEntry]:
    """
    改用JSON資料直接擷取，取代不可靠的HTML表格DOM解析。

    這個系統背後其實是用JSON資料驅動畫面渲染（ExtJS Grid元件），
    角色代碼列的格式固定是：
        "teamMemberName":"角色代碼","team_description":{...}
    （成員列的 teamMemberName 是物件而非純字串，所以這個pattern
      天然只會比對到角色列，不會誤比對到成員姓名）

    找到角色代碼後，往後找該角色底下第一筆的 "tooltip":"姓名"
    （成員姓名）以及 "joined_proj" 的 是/否。
    """
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
    """
    將解析結果轉為 {角色代碼: 成員帳號} dict，供 fallback_resolver 直接使用。
    Dummy / 未指派角色不會出現在回傳的 dict 中（視同查無資料）。
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


def _fetch_pdm_team_impl(playwright_instance, username: str, password: str,
                          project_number: str, headless: bool) -> dict[str, str]:
    browser = playwright_instance.chromium.launch(headless=headless)
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


def fetch_pdm_team(username: str, password: str, project_number: str,
                    headless: bool = True, playwright_instance=None) -> dict[str, str]:
    """
    完整流程入口：登入 -> 搜尋專案 -> 進入小組頁 -> 解析 -> 回傳角色/成員 dict

    帳密透過 browser.new_context(http_credentials=...) 帶入（這是
    唯一驗證有效的方式，因為這個系統走HTTP層級驗證，不是網頁帳密
    表單），由呼叫端透過 admin_gate.request_pdm_credentials() 即時
    取得，此函式不主動要求輸入，也不落地儲存帳密。

    playwright_instance 參數：如果呼叫端（例如main.py）已經有一個
    執行中的 sync_playwright() 環境（例如同時還開著JIRA的瀏覽器），
    必須把那個環境傳進來共用，不能讓這個函式自己再另外開一個
    ——Playwright不支援「環境套環境」，硬是這樣做會丟出
    「using Playwright Sync API inside the asyncio loop」錯誤。
    只有在完全獨立執行（例如test_e2e_pdm_to_loader.py單獨測試PDM時）
    才不用傳這個參數，讓函式自己開一個新環境即可。
    """
    if playwright_instance is not None:
        return _fetch_pdm_team_impl(
            playwright_instance, username, password, project_number, headless
        )
    with sync_playwright() as p:
        return _fetch_pdm_team_impl(p, username, password, project_number, headless)