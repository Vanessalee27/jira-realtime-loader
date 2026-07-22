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

# RD 回覆格式：支援三種寫法
#   1. "REVIEWER: A" 或 "REVIEWER（SW RQM）: A"  -> 選邏輯1(PDM)
#   2. "REVIEWER: B" 或 "REVIEWER（SW RQM）: B"  -> 選邏輯2(JIRA)
#   3. "REVIEWER（SW RQM）: John_Doe"            -> RD自行輸入姓名
REPLY_SEGMENT_PATTERN = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:（[^）]*）)?\s*[:：]\s*([^,\n]+)"
)


def _mention_prefix(assignee_full_name: str, reporter_full_name: str) -> str:
    def _strip_chinese_suffix(full_name: str) -> str:
        idx = full_name.find("(")
        if idx == -1:
            idx = full_name.find("（")
        return full_name[:idx].strip() if idx != -1 else full_name.strip()

    assignee_account = normalize_name(_strip_chinese_suffix(assignee_full_name))
    reporter_account = normalize_name(_strip_chinese_suffix(reporter_full_name))
    return f"[~{assignee_account}] [~{reporter_account}]\n\n"


def build_initial_comment(resolved: dict[str, FieldResult],
                           assignee_full_name: str, reporter_full_name: str) -> str:
    """
    通知對象與內容規則：
      - 全部欄位比對成功（無衝突）-> @Assignee，直接附上乾淨的loader.txt
        內容（不含來源標籤等備註，備註只適合開發除錯時看，不適合
        當作正式交付內容）
      - 有任何欄位衝突 -> @Reporter，附上三選項讓Reporter擇一回覆
    """
    conflict_fields = [r for r in resolved.values() if r.status == FieldStatus.CONFLICT]
    missing_fields = [r for r in resolved.values() if r.status == FieldStatus.MISSING]
    has_conflict = len(conflict_fields) > 0

    def _strip_chinese_suffix(full_name: str) -> str:
        idx = full_name.find("(")
        if idx == -1:
            idx = full_name.find("（")
        return full_name[:idx].strip() if idx != -1 else full_name.strip()

    if has_conflict:
        mention_account = normalize_name(_strip_chinese_suffix(reporter_full_name))
    else:
        mention_account = normalize_name(_strip_chinese_suffix(assignee_full_name))

    lines = [f"[~{mention_account}]", ""]

    if not has_conflict:
        lines.append(AI_COMMENT_MARKER + "，流簽人員已確認完成，loader.txt 內容如下：")
        lines.append("")
        for name, r in resolved.items():
            value = r.value if r.value else "[待確認]"
            lines.append(f"{name}\t{value}")
        if missing_fields:
            lines.append("")
            lines.append("【提醒】以下角色於 PDM / JIRA 均查無對應人員，"
                          "請協助更新 PDM 專案成員：")
            for r in missing_fields:
                lines.append(f"- {r.field_name}（{r.role_code}）")
        lines.append("")
        lines.append(f"執行時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        return "\n".join(lines)

    lines.append(AI_COMMENT_MARKER + "，發現 PDM 與 JIRA 資料不一致，請確認：")
    lines.append("")
    lines.append("【本次 Loader 執行結果】")
    for name, r in resolved.items():
        if r.status in (FieldStatus.AGREED, FieldStatus.SINGLE_SOURCE):
            label = SOURCE_LABEL.get(r.resolved_by, r.resolved_by)
            lines.append(f"- {name}（{r.role_code}）: {r.value} [{label}]")
        elif r.status == FieldStatus.CONFLICT:
            lines.append(f"- {name}（{r.role_code}）: ⚠️ 待選擇（PDM 與 JIRA 資料不一致）")
        else:
            lines.append(f"- {name}（{r.role_code}）: ⚠️ 待確認（PDM 與 JIRA 皆查無資料）")

    lines.append("")
    lines.append("【需要 Reporter 確認】以下欄位 PDM 與 JIRA 資料不一致，"
                  "請從三個選項中擇一回覆：")
    for r in conflict_fields:
        lines.append(f"- {r.field_name}（{r.role_code}）: "
                      f"A）{r.logic1_value}（PDM即時資料）  "
                      f"或  B）{r.logic2_value}（JIRA內建簽核表）")
    lines.append("")
    lines.append("回覆方式：")
    lines.append("  選項1或2，請回覆，例如：")
    lines.append("  " + "\n  ".join(f"{r.field_name}: A" for r in conflict_fields))
    lines.append("")
    lines.append("  選項3（都不對，我自己指定人選），請照這個格式回覆"
                  "（outlook 帳號格式為 名_姓）：")
    lines.append("  " + "\n  ".join(
        f"{r.field_name}（{r.role_code}）: outlook 名_姓" for r in conflict_fields
    ))

    if missing_fields:
        lines.append("")
        lines.append("【需要人工確認】以下角色於 PDM / JIRA 均查無對應人員，請協助更新 PDM 專案成員：")
        for r in missing_fields:
            lines.append(f"- {r.field_name}（{r.role_code}）")

    lines.append("")
    lines.append(f"執行時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return "\n".join(lines)


def build_final_comment(resolved: dict[str, FieldResult],
                         assignee_full_name: str, reporter_full_name: str) -> str:
    """
    第二階段留言：套用RD選擇後，公告最終結果。
    """
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
    """
    從RD的回覆留言文字中解析出各衝突欄位的回覆內容。

    支援三種寫法（見 REPLY_SEGMENT_PATTERN）：
      - "REVIEWER: A" 或 "REVIEWER（SW RQM）: A" -> 選邏輯1(PDM)
      - "REVIEWER: B" 或 "REVIEWER（SW RQM）: B" -> 選邏輯2(JIRA)
      - "REVIEWER（SW RQM）: John_Doe"          -> RD自行輸入姓名（選項3）
    """
    result = {}
    for m in REPLY_SEGMENT_PATTERN.finditer(comment_text):
        field_name, raw_value = m.group(1), m.group(2).strip()
        if field_name not in conflict_field_names:
            continue
        raw_value = re.sub(r"\s*\[[^\]]*\]\s*$", "", raw_value).strip()
        if not raw_value:
            continue
        result[field_name] = raw_value
    return result


def post_new_comment(page, ticket_id: str, content: str) -> None:
    """
    每次都新增一則留言，不做編輯/去重。

    留言輸入框是TinyMCE富文本編輯器（渲染成獨立iframe），需要先切換到
    那個iframe，改用TinyMCE自己的JavaScript API設定內容，確保JIRA能
    正確偵測到輸入，讓「新增」按鈕的disabled狀態解除。
    """
    page.goto(f"https://jira-dc.moxa.com/browse/{ticket_id}")
    page.click("a#footer-comment-button")

    page.wait_for_selector("iframe[id^='mce_'][id$='_ifr']", timeout=15000)

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