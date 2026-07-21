"""
JIRA 通知模組。
"""

from __future__ import annotations

import re
from datetime import datetime

from src.utils.naming import normalize_name
from src.logic.fallback_resolver import FieldResult, FieldStatus

AI_COMMENT_MARKER = "此訊息由 AI 自動產生"

SOURCE_LABEL = {
    "logic1": "PDM即時資料",
    "logic2": "JIRA內建簽核表(備援)",
    "logic1_and_logic2_agree": "PDM與JIRA一致",
    "rd_choice_A_logic1": "RD人工選擇(採PDM)",
    "rd_choice_B_logic2": "RD人工選擇(採JIRA)",
}

REPLY_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*[:：]\s*([AaBb])\b")


def _mention_prefix(assignee_full_name: str, reporter_full_name: str) -> str:
    assignee_account = normalize_name(assignee_full_name)
    reporter_account = normalize_name(reporter_full_name)
    return f"[~{assignee_account}] [~{reporter_account}]\n\n"


def build_initial_comment(resolved: dict[str, FieldResult],
                           assignee_full_name: str, reporter_full_name: str) -> str:
    lines = [
        _mention_prefix(assignee_full_name, reporter_full_name).rstrip(),
        "",
        AI_COMMENT_MARKER + "，請確認執行結果是否符合預期。",
        "",
        "【本次 Loader 執行結果】",
    ]

    conflict_fields = []

    for name, r in resolved.items():
        if r.status in (FieldStatus.AGREED, FieldStatus.SINGLE_SOURCE):
            label = SOURCE_LABEL.get(r.resolved_by, r.resolved_by)
            lines.append(f"- {name}（{r.role_code}）: {r.value} [{label}]")
        elif r.status == FieldStatus.CONFLICT:
            lines.append(f"- {name}（{r.role_code}）: ⚠️ 待選擇（PDM 與 JIRA 資料不一致）")
            conflict_fields.append(r)
        else:
            lines.append(f"- {name}（{r.role_code}）: ⚠️ 待確認（PDM 與 JIRA 皆查無資料）")

    if conflict_fields:
        lines.append("")
        lines.append("【需要 RD 選擇】以下欄位 PDM 與 JIRA 資料不一致，請回覆對應選項：")
        for r in conflict_fields:
            lines.append(f"- {r.field_name}: A）{r.logic1_value}（PDM）"
                          f"  或  B）{r.logic2_value}（JIRA）")
        lines.append("")
        lines.append("請直接回覆，例如：")
        lines.append("  " + "\n  ".join(f"{r.field_name}: A" for r in conflict_fields))

    missing_fields = [r for r in resolved.values() if r.status == FieldStatus.MISSING]
    if missing_fields:
        lines.append("")
        lines.append("【需要人工確認】以下角色於 PDM / JIRA 均查無對應人員，請協助更新 PDM 專案成員：")
        for r in missing_fields:
            lines.append(f"- {r.field_name}（{r.role_code}）")

    if not conflict_fields and not missing_fields:
        lines.append("")
        lines.append("✅ 所有角色皆已成功比對，無需人工確認。")

    lines.append("")
    lines.append(f"執行時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return "\n".join(lines)


def build_final_comment(resolved: dict[str, FieldResult],
                         assignee_full_name: str, reporter_full_name: str) -> str:
    lines = [
        _mention_prefix(assignee_full_name, reporter_full_name).rstrip(),
        "",
        AI_COMMENT_MARKER + "，已依 RD 回覆更新最終結果：",
        "",
        "【最終 loader.txt 內容】",
    ]
    for name, r in resolved.items():
        label = SOURCE_LABEL.get(r.resolved_by, r.resolved_by or "待確認")
        value = r.value if r.value else "[待確認]"
        lines.append(f"- {name}（{r.role_code}）: {value} [{label}]")

    lines.append("")
    lines.append(f"確認時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return "\n".join(lines)


def parse_rd_reply(comment_text: str, conflict_field_names: list[str]) -> dict[str, str]:
    matches = REPLY_PATTERN.findall(comment_text)
    result = {}
    for field_name, choice in matches:
        if field_name in conflict_field_names:
            result[field_name] = choice.upper()
    return result


def post_new_comment(page, ticket_id: str, content: str) -> None:
    """
    每次都新增一則留言，不做編輯/去重。
    """
    page.goto(f"https://jira-dc.moxa.com/browse/{ticket_id}")
    page.click("a#footer-comment-button")

    page.wait_for_selector("iframe[id^='mce_'][id$='_ifr']", timeout=15000)

    # 改用TinyMCE自己的JavaScript API，確保JIRA能正確偵測到輸入，
    # 讓「新增」按鈕的disabled狀態解除。
    page.evaluate(
        """(content) => {
            if (window.tinymce && window.tinymce.activeEditor) {
                window.tinymce.activeEditor.setContent(content);
                window.tinymce.activeEditor.fire('change');
                window.tinymce.activeEditor.fire('keyup');
            }
        }""",
        content,
    )
    page.wait_for_timeout(500)

    editor_frame = page.frame_locator("iframe[id^='mce_'][id$='_ifr']")
    editor_body = editor_frame.locator("body")
    current_text = editor_body.inner_text().strip()
    if content not in current_text:
        editor_body.click()
        editor_body.fill(content)
        page.wait_for_timeout(500)

    submit_selectors = [
        "#issue-comment-add-submit",
        "button:has-text('新增')",
        "button:has-text('儲存')",
        "button:has-text('Save')",
        "input[type='submit']",
        "button:has-text('Add')",
    ]

    for sel in submit_selectors:
        btn = page.locator(sel)
        if btn.count() == 0:
            continue
        try:
            btn.first.wait_for(state="visible", timeout=2000)
        except Exception:
            continue
        for _ in range(10):
            if btn.first.is_visible() and btn.first.is_enabled():
                btn.first.click()
                return
            page.wait_for_timeout(500)

    raise Exception("找不到可點擊(enabled)的留言送出按鈕，已嘗試多種常見文字皆無效")


def fetch_latest_comments(page, ticket_id: str) -> list[str]:
    page.goto(f"https://jira-dc.moxa.com/browse/{ticket_id}")
    comment_elements = page.query_selector_all(".comment-body")
    return [el.inner_text() for el in comment_elements]