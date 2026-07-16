"""
共用命名正規化工具。

規則：First name（含中間縮寫）空格取消 + '_' + Last name
範例：'Nicky TT Lin' -> 'NickyTT_Lin'

此規則同時適用於：
  1. PDM 專案成員顯示格式
  2. loader.txt 輸出格式
  3. JIRA @mention 帳號格式（[~帳號]）
三處共用同一套規則與同一個函式，避免各模組各自實作造成不一致。
"""

from __future__ import annotations


def normalize_name(full_name: str) -> str:
    """
    將人員全名轉換為系統統一格式。

    Args:
        full_name: 原始全名，例如 'Nicky TT Lin' 或 'Johnny HT Lee'。
                   若只有單一詞（無空格），原樣回傳。

    Returns:
        正規化後的字串，例如 'NickyTT_Lin'。

    Raises:
        ValueError: 輸入為空字串或全為空白。
    """
    if not full_name or not full_name.strip():
        raise ValueError("full_name 不可為空")

    parts = full_name.strip().split()
    if len(parts) == 1:
        return parts[0]

    *first_parts, last_name = parts
    first_name = "".join(first_parts)
    return f"{first_name}_{last_name}"


def normalize_multi(names_str: str, delimiter: str = " ") -> str:
    """
    處理同一角色欄位存在多人全名混合的狀況，
    逐一切分、正規化後以指定分隔符重新組合。

    Args:
        names_str: 可能包含多人全名的字串，例如
                    'ElmerBX Huang Wade Huang' 這類需先手動確認切分邏輯的情況。
        delimiter: 輸出時使用的分隔符，預設為空白。

    Returns:
        正規化後、以 delimiter 串接的字串。

    Note:
        自動切分多人全名有高度歧義風險（無法區分是同一人的中間名
        還是不同人的姓氏），若輸入來源明確以逗號分隔多人，
        請優先使用逗號切分而非本函式的空白啟發式規則。
    """
    if "," in names_str:
        candidates = [n.strip() for n in names_str.split(",") if n.strip()]
    else:
        candidates = [names_str]

    normalized = [normalize_name(n) for n in candidates]
    return delimiter.join(normalized)


if __name__ == "__main__":
    # 快速自我驗證，使用真實案例資料
    test_cases = [
        ("Nicky TT Lin", "NickyTT_Lin"),
        ("Johnny HT Lee", "JohnnyHT_Lee"),
        ("Hank CY Wu", "HankCY_Wu"),
        ("Banson Chen", "Banson_Chen"),
        ("ViviTW_Yang", "ViviTW_Yang"),  # 已是正規化格式，應保持不變的情況需另行判斷
    ]
    for raw, expected in test_cases:
        result = normalize_name(raw)
        status = "OK" if result == expected else "MISMATCH"
        print(f"[{status}] normalize_name({raw!r}) = {result!r} (expected {expected!r})")
