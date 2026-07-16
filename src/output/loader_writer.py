"""
loader.txt 輸出模組。

輸出規則：
  - AGREED / SINGLE_SOURCE：正常輸出值
  - CONFLICT：輸出 [待選擇]（等RD在JIRA回覆A或B）
  - MISSING：輸出 [待確認]（兩邊都查無資料，需RD補PDM或確認）
"""

from __future__ import annotations

from pathlib import Path
from src.logic.fallback_resolver import FieldResult, FieldStatus


def format_field_line(result: FieldResult) -> str:
    if result.status in (FieldStatus.AGREED, FieldStatus.SINGLE_SOURCE):
        return f"{result.field_name}\t{result.value}"
    if result.status == FieldStatus.CONFLICT:
        return f"{result.field_name}\t[待選擇]"
    return f"{result.field_name}\t[待確認]"


def write_loader_txt(resolved: dict[str, FieldResult],
                      output_path: str | Path = "loader.txt") -> tuple[str, dict]:
    """
    產出 loader.txt，並回傳 (檔案內容, 摘要字典)
    摘要字典分類出 conflict_fields 與 missing_fields，方便上層決定要不要觸發通知。
    """
    lines = [format_field_line(r) for r in resolved.values()]
    content = "\n".join(lines) + "\n"

    output_path = Path(output_path)
    output_path.write_text(content, encoding="utf-8")

    summary = {
        "conflict_fields": [r.field_name for r in resolved.values()
                             if r.status == FieldStatus.CONFLICT],
        "missing_fields": [r.field_name for r in resolved.values()
                            if r.status == FieldStatus.MISSING],
        "resolved_fields": [r.field_name for r in resolved.values()
                             if r.status in (FieldStatus.AGREED, FieldStatus.SINGLE_SOURCE)],
    }
    return content, summary
