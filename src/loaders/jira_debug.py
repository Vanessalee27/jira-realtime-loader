"""
jira_debug.py

JIRA 爬蟲除錯專用工具，只在需要排查問題時才匯入使用。
"""

from __future__ import annotations

from playwright.sync_api import Page, Frame


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
                ivisible = inp.is_visible()
                print(f"  [ctx={ctx_i}] type={itype!r} name={iname!r} "
                      f"id={iid!r} visible={ivisible}")
            except Exception as e:
                print(f"  [ctx={ctx_i}] (讀取屬性失敗: {e})")
    print(f"\n[診斷] 總共找到 {total} 個 <input> 元素")


def debug_dump_html(page: Page, filepath: str) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(page.content())
    print(f"\n[診斷] 已將完整頁面原始碼存至：{filepath}")


def debug_print_field_context(page: Page, keyword: str, context_chars: int = 500) -> None:
    """
    診斷用：在頁面原始碼裡找出keyword出現的位置，印出前後文字，
    方便確認自訂欄位（如M_PDM Project Number）附近的HTML真實結構。
    """
    html = page.content()
    idx = html.find(keyword)
    if idx == -1:
        print(f"[診斷] 頁面原始碼中找不到「{keyword}」")
        return
    print(f"[診斷]「{keyword}」出現位置附近的HTML：")
    print(repr(html[max(0, idx - 100):idx + context_chars]))