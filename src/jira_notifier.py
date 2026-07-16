"""
JIRA 通知模組。

處理兩種留言：
  1. 初次執行結果留言：列出所有欄位狀態，衝突欄位附上 A/B 選項與回覆格式說明
  2. RD回覆後的最終結果留言：套用RD選擇後，公告最終loader.txt內容

RD 回覆解析：
  掃描留言區塊，尋找符合 "欄位名: A" 或 "欄位名: B" 格式的文字
  （不區分大小寫、允許前後空白），逐一比對衝突欄位清單。
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

# RD 回覆格式範例： "REVIEWER3: A" 或 "REVIEWER3：B"（全形/半形冒號皆支援）
REPLY_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*[:：]\s*([AaBb])\b")


def _mention_prefix(assignee_full_name: str, reporter_full_name: str) -> str:
    assignee_account = normalize_name(assignee_full_name)
    reporter_account = normalize_name(reporter_full_name)
    return f"[~{assignee_account}] [~{reporter_account}]\n\n"


def build_initial_comment(resolved: dict[str, FieldResult],
                           assignee_full_name: str, reporter_full_name: str) -> str:
    """
    第一階段留言：列出所有欄位結果，衝突欄位附上選項與回覆說明。
    """
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
        else:  # MISSING
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
    從RD的回覆留言文字中解析出各衝突欄位選擇的 A 或 B。

    Args:
        comment_text: RD回覆的原始留言文字
        conflict_field_names: 需要被解析的衝突欄位名稱清單

    Returns:
        {欄位名稱: 'A' 或 'B'}，只包含成功解析到的欄位；
        若RD只回覆了部分欄位，其餘欄位不會出現在回傳結果中
        （代表尚待進一步回覆，呼叫端需自行判斷是否要再次提醒）。
    """
    matches = REPLY_PATTERN.findall(comment_text)
    result = {}
    for field_name, choice in matches:
        if field_name in conflict_field_names:
            result[field_name] = choice.upper()
    return result


def post_new_comment(page, ticket_id: str, content: str) -> None:
    """每次都新增一則留言，不做編輯/去重（依需求確認的策略）"""
    page.goto(f"https://jira-dc.moxa.com/browse/{ticket_id}")
    page.click("a#footer-comment-button")
    page.fill(".comment-textarea", content)
    page.click("button:has-text('新增')")


def fetch_latest_comments(page, ticket_id: str) -> list[str]:
    """
    抓取票的所有留言文字（供輪詢RD是否已回覆時使用）。
    實際 selector 需依 JIRA-DC 頁面結構調整。
    """
    page.goto(f"https://jira-dc.moxa.com/browse/{ticket_id}")
    comment_elements = page.query_selector_all(".comment-body")
    return [el.inner_text() for el in comment_elements]
