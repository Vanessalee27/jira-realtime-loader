"""
欄位解析核心邏輯。

三種可能結果：
  1. single_source   ：只有邏輯1(PDM)或邏輯2(JIRA)其中一邊有值 -> 自動採用
  2. agreed          ：兩邊都有值且相同 -> 自動採用
  3. conflict        ：兩邊都有值但不同 -> 交由 RD 人工選擇（原「邏輯3」已改為此用途）
  4. missing         ：兩邊皆查無 -> 標記待確認，走原本 Step4 人工確認流程
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FieldStatus(str, Enum):
    SINGLE_SOURCE = "single_source"
    AGREED = "agreed"
    CONFLICT = "conflict"
    MISSING = "missing"


@dataclass
class FieldResult:
    field_name: str
    role_code: str
    status: FieldStatus
    value: str | None = None          # single_source / agreed 時有值
    logic1_value: str | None = None   # PDM 查詢結果（可能為 None）
    logic2_value: str | None = None   # JIRA 內建表結果（可能為 None）
    resolved_by: str | None = None    # 記錄最終是誰選的：'logic1' / 'logic2' / 'rd_choice_A' / 'rd_choice_B'


ROLE_MAPPING = {
    "REVIEWER": "SW RQM",
    "REVIEWER2": "PE NP",
    "REVIEWER3": "SW STM",
    "APPROVER": "SW PJM UM",
    "Mail_Reveiver": "PE NP UM",
}


def resolve_field(field_name: str, role_code: str,
                   pdm_team: dict, jira_table: dict | None) -> FieldResult:
    """
    針對單一欄位，比對邏輯1(PDM)與邏輯2(JIRA)的結果，判斷狀態。
    """
    logic1_value = pdm_team.get(role_code)
    logic1_value = logic1_value.strip() if logic1_value else None

    logic2_value = None
    if jira_table:
        raw = jira_table.get(field_name)
        logic2_value = raw.strip() if raw else None

    has1 = bool(logic1_value)
    has2 = bool(logic2_value)

    if has1 and has2:
        if logic1_value == logic2_value:
            return FieldResult(
                field_name=field_name, role_code=role_code,
                status=FieldStatus.AGREED, value=logic1_value,
                logic1_value=logic1_value, logic2_value=logic2_value,
                resolved_by="logic1_and_logic2_agree",
            )
        else:
            # 衝突，暫不給 value，等 RD 選擇
            return FieldResult(
                field_name=field_name, role_code=role_code,
                status=FieldStatus.CONFLICT, value=None,
                logic1_value=logic1_value, logic2_value=logic2_value,
                resolved_by=None,
            )

    if has1:
        return FieldResult(
            field_name=field_name, role_code=role_code,
            status=FieldStatus.SINGLE_SOURCE, value=logic1_value,
            logic1_value=logic1_value, logic2_value=None,
            resolved_by="logic1",
        )

    if has2:
        return FieldResult(
            field_name=field_name, role_code=role_code,
            status=FieldStatus.SINGLE_SOURCE, value=logic2_value,
            logic1_value=None, logic2_value=logic2_value,
            resolved_by="logic2",
        )

    return FieldResult(
        field_name=field_name, role_code=role_code,
        status=FieldStatus.MISSING, value=None,
        logic1_value=None, logic2_value=None, resolved_by=None,
    )


def resolve_loader(pdm_team: dict, jira_table: dict | None) -> dict[str, FieldResult]:
    """逐欄位解析，回傳 {field_name: FieldResult}"""
    return {
        field_name: resolve_field(field_name, role_code, pdm_team, jira_table)
        for field_name, role_code in ROLE_MAPPING.items()
    }


def apply_rd_choice(result: FieldResult, choice: str) -> FieldResult:
    """
    RD 回覆選擇後，套用選擇結果更新 FieldResult。

    Args:
        result: 原本 status=CONFLICT 的 FieldResult
        choice: 'A' 代表選邏輯1的值，'B' 代表選邏輯2的值
    """
    if result.status != FieldStatus.CONFLICT:
        raise ValueError(f"欄位 {result.field_name} 並非衝突狀態，無法套用RD選擇")

    choice = choice.strip().upper()
    if choice == "A":
        result.value = result.logic1_value
        result.resolved_by = "rd_choice_A_logic1"
    elif choice == "B":
        result.value = result.logic2_value
        result.resolved_by = "rd_choice_B_logic2"
    else:
        raise ValueError(f"無效的選擇：{choice}，僅接受 A 或 B")

    result.status = FieldStatus.AGREED  # 視為已解決
    return result
