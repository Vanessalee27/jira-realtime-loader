"""
pdm_debug.py

PDM 爬蟲除錯專用工具，只在需要排查問題時才匯入使用，
不影響正式主流程（pdm_reader.py）的執行。
"""

from __future__ import annotations

from playwright.sync_api import Page, Frame


def debug_print_frames(page: Page) -> None:
    """印出目前頁面所有 frame 的名稱與網址，方便判斷是否為frameset架構"""
    print(f"\n[診斷] 目前頁面共有 {len(page.frames)} 個 frame：")
    for i, f in enumerate(page.frames):
        print(f"  frame[{i}] name={f.name!r} url={f.url}")


def debug_print_inputs(page: Page) -> None:
    """列出頁面（含所有frame）上所有 <input> 元素的屬性"""
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
    """列出頁面（含所有frame）上所有文字內容「包含」keyword的 <a> 連結"""
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


def debug_dump_html(page: Page, filepath: str) -> None:
    """把當下完整頁面原始碼存成檔案，方便直接上傳分析"""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(page.content())
    print(f"\n[診斷] 已將完整頁面原始碼存至：{filepath}")