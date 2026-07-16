"""
Admin Gate 模組

治理原則：
  1. 任何機敏資料（API Key、PDM 帳密）不寫死在程式碼或 .env，
     每次需要時由管理員手動輸入，僅存於當次 session 記憶體。
  2. 管理員密碼以 bcrypt hash 儲存，絕不存明碼。
  3. 密碼驗證通過後才能繼續後續流程（進入 RAG 平台前的守門機制）。
"""

from __future__ import annotations

import os
import getpass
from pathlib import Path
from dataclasses import dataclass

try:
    import bcrypt
except ImportError as e:
    raise ImportError(
        "缺少 bcrypt 套件，請先執行：pip install bcrypt --break-system-packages"
    ) from e


PASSWORD_HASH_FILE = Path(__file__).resolve().parent.parent.parent / "config" / ".admin_password_hash"

AVAILABLE_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
]


@dataclass
class Session:
    """
    當次執行的 session 記憶體。
    所有機敏欄位只存在於這裡，程式結束即消失，絕不落地寫檔。
    """
    unlocked: bool = False
    api_key: str | None = None
    pdm_username: str | None = None
    pdm_password: str | None = None
    selected_model: str | None = None

    def clear_secrets(self) -> None:
        """執行結束時主動清除記憶體中的機敏資料"""
        self.api_key = None
        self.pdm_username = None
        self.pdm_password = None


class AdminGateError(Exception):
    """Admin Gate 相關錯誤的基底類別"""
    pass


def _load_password_hash() -> bytes | None:
    if PASSWORD_HASH_FILE.exists():
        return PASSWORD_HASH_FILE.read_bytes()
    return None


def _save_password_hash(hash_bytes: bytes) -> None:
    PASSWORD_HASH_FILE.parent.mkdir(parents=True, exist_ok=True)
    PASSWORD_HASH_FILE.write_bytes(hash_bytes)
    try:
        os.chmod(PASSWORD_HASH_FILE, 0o600)
    except OSError:
        pass  # Windows 環境可能不支援 chmod，忽略即可


def setup_password(min_length: int = 8) -> bytes:
    """首次啟動，強制要求管理員設定密碼"""
    print("[Admin Gate] 尚未設定管理員密碼，請設定新密碼。")
    while True:
        pw1 = getpass.getpass("設定密碼：")
        pw2 = getpass.getpass("再次輸入確認：")
        if pw1 != pw2:
            print("兩次輸入不一致，請重新輸入。")
            continue
        if len(pw1) < min_length:
            print(f"密碼長度至少需 {min_length} 碼，請重新輸入。")
            continue
        break
    hash_bytes = bcrypt.hashpw(pw1.encode("utf-8"), bcrypt.gensalt())
    _save_password_hash(hash_bytes)
    print("[Admin Gate] 密碼設定完成。")
    return hash_bytes


def change_password(current_hash: bytes) -> bytes:
    """變更密碼，需先驗證舊密碼才能繼續"""
    old_pw = getpass.getpass("請輸入目前密碼以驗證身份：")
    if not bcrypt.checkpw(old_pw.encode("utf-8"), current_hash):
        raise AdminGateError("目前密碼錯誤，無法變更。")
    print("[Admin Gate] 驗證成功，請設定新密碼。")
    return setup_password()


def verify_password(max_attempts: int = 3) -> bool:
    """
    管理員登入驗證，並支援輸入 'change' 進入變更密碼流程。
    最多允許 max_attempts 次錯誤嘗試，超過則中止（防止暴力破解）。
    """
    stored_hash = _load_password_hash()
    if stored_hash is None:
        setup_password()
        return True

    for attempt in range(1, max_attempts + 1):
        choice = input("請輸入密碼（或輸入 'change' 變更密碼）：").strip()
        if choice.lower() == "change":
            change_password(stored_hash)
            return True

        pw = choice if choice else getpass.getpass("密碼：")
        if bcrypt.checkpw(pw.encode("utf-8"), stored_hash):
            print("[Admin Gate] 驗證成功，已解鎖。")
            return True

        remaining = max_attempts - attempt
        print(f"[Admin Gate] 密碼錯誤，剩餘 {remaining} 次機會。")

    print("[Admin Gate] 已達最大嘗試次數，系統中止。")
    return False


def request_api_key(env_var_name: str = "YOUR_API_KEY") -> str:
    """
    檢查環境變數是否為預設無效值（YOUR_API_KEY / XXXXX / 空值），
    若是則要求管理員手動輸入，僅回傳於記憶體中使用，不寫回 .env。
    """
    placeholder_values = {"", "XXXXX", "YOUR_API_KEY", "your_api_key"}
    current = os.environ.get(env_var_name, "")

    if current and current not in placeholder_values:
        print(f"[Admin Gate] 偵測到環境變數 {env_var_name} 已設定有效值，沿用現有值。")
        return current

    print(f"[Admin Gate] {env_var_name} 尚未設定或為預設無效值 (XXXXX)。")
    key = getpass.getpass(f"請管理員手動輸入 {env_var_name}：").strip()
    if not key:
        raise AdminGateError("API Key 不可為空，系統中止。")
    return key


def request_pdm_credentials() -> tuple[str, str]:
    """
    需要存取 PDM（Windchill）時，即時要求管理員輸入帳密。
    僅存於 session 記憶體，絕不落地寫檔或寫入 log。
    """
    print("[Admin Gate] 需要 PDM 存取權限，請管理者輸入帳密。")
    username = input("PDM 帳號：").strip()
    password = getpass.getpass("PDM 密碼：")
    if not username or not password:
        raise AdminGateError("PDM 帳密不可為空，系統中止。")
    return username, password


def select_model() -> str:
    """選擇 Google AI Studio 模型"""
    print("[Admin Gate] 請選擇要使用的模型：")
    for i, m in enumerate(AVAILABLE_MODELS, start=1):
        print(f"  {i}. {m}")

    while True:
        choice = input("輸入編號：").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(AVAILABLE_MODELS):
            selected = AVAILABLE_MODELS[int(choice) - 1]
            print(f"[Admin Gate] 已選擇模型：{selected}")
            return selected
        print("輸入無效，請重新輸入。")


def enter_system(require_model_selection: bool = True) -> Session:
    """
    完整 Admin Gate 流程入口：
      密碼驗證 -> API Key 輸入 -> (選擇模型) -> 核准進入主系統

    PDM 帳密不在此流程中要求，而是在主流程真正需要查詢 PDM 時
    才呼叫 request_pdm_credentials()，避免不需要 PDM 的任務也被要求輸入。
    """
    session = Session()

    if not verify_password():
        raise SystemExit("[Admin Gate] 驗證失敗，系統中止。")
    session.unlocked = True

    session.api_key = request_api_key()

    if require_model_selection:
        session.selected_model = select_model()

    print("[Admin Gate] 核准通過，進入主系統。")
    return session
