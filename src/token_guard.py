"""
Token Guard 模組

治理原則：
  1. 所有上限皆從 config/settings.yaml 讀取，Agent 本身不得自行調高。
  2. 修改上限僅限管理者，且需記錄於 docs/CHANGELOG.md。
  3. 單次執行與每日累計皆有獨立 hard limit，任一超過即中止（若 hard_stop=True）。
"""

from __future__ import annotations

import json
import yaml
from pathlib import Path
from datetime import date
from dataclasses import dataclass, field

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
USAGE_LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "token_usage.json"


class TokenBudgetExceeded(Exception):
    """觸發 hard limit 時拋出，呼叫端須中止當次任務並記錄 log"""
    pass


@dataclass
class TokenGuard:
    max_tokens_per_run: int
    max_tokens_per_day: int
    warning_threshold_pct: int
    hard_stop: bool
    used_this_run: int = field(default=0, init=False)

    @classmethod
    def from_config(cls, config_path: Path = CONFIG_PATH) -> "TokenGuard":
        if not config_path.exists():
            raise FileNotFoundError(f"找不到設定檔：{config_path}")
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)["token_budget"]
        return cls(
            max_tokens_per_run=cfg["max_tokens_per_run"],
            max_tokens_per_day=cfg["max_tokens_per_day"],
            warning_threshold_pct=cfg["warning_threshold_pct"],
            hard_stop=cfg["hard_stop"],
        )

    def _load_daily_usage(self) -> int:
        if not USAGE_LOG_PATH.exists():
            return 0
        with open(USAGE_LOG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") != str(date.today()):
            return 0  # 跨日自動歸零
        return int(data.get("used_tokens", 0))

    def _save_daily_usage(self, used_tokens: int) -> None:
        USAGE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(USAGE_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump({"date": str(date.today()), "used_tokens": used_tokens}, f)

    def estimate_tokens(self, text: str) -> int:
        """
        簡易估算（中英混合文字，粗估每 2 字元約 1 token）。
        正式串接 Gemini 時建議改用官方 tokenizer 取得精確值，
        此函式僅作為呼叫前的保守預估，避免送出明顯超量的請求。
        """
        return max(1, len(text) // 2)

    def check_and_record(self, tokens_used: int) -> None:
        """
        每次呼叫 LLM 後記錄花費，並檢查是否超過上限。
        超過任一 hard limit 且 hard_stop=True 時，拋出 TokenBudgetExceeded。
        """
        self.used_this_run += tokens_used

        if self.used_this_run > self.max_tokens_per_run:
            msg = (f"單次執行 Token 使用量 {self.used_this_run} "
                   f"已超過上限 {self.max_tokens_per_run}")
            if self.hard_stop:
                raise TokenBudgetExceeded(msg)
            print(f"[Token Guard][WARNING] {msg}")

        daily_used = self._load_daily_usage() + tokens_used
        self._save_daily_usage(daily_used)

        warn_at = self.max_tokens_per_day * self.warning_threshold_pct / 100
        if daily_used >= warn_at:
            pct = daily_used / self.max_tokens_per_day
            print(f"[Token Guard][WARNING] 今日累計已使用 "
                  f"{daily_used}/{self.max_tokens_per_day} tokens ({pct:.0%})")

        if daily_used > self.max_tokens_per_day:
            msg = f"今日累計 Token 使用量 {daily_used} 已超過每日上限 {self.max_tokens_per_day}"
            if self.hard_stop:
                raise TokenBudgetExceeded(msg)
            print(f"[Token Guard][WARNING] {msg}")

    def guarded_call(self, prompt_text: str, llm_call_fn):
        """
        便利包裝：呼叫前估算 prompt token 並預先檢查，
        呼叫後再依實際回傳的 usage 資訊記錄真實花費。

        Args:
            prompt_text: 要送出的 prompt，用於呼叫前的保守預估。
            llm_call_fn: 實際呼叫 LLM 的函式，需回傳
                         (response, actual_tokens_used) 的 tuple。

        Returns:
            llm_call_fn 回傳的 response。
        """
        estimated = self.estimate_tokens(prompt_text)
        if self.used_this_run + estimated > self.max_tokens_per_run and self.hard_stop:
            raise TokenBudgetExceeded(
                f"預估花費 {estimated} tokens 將超過單次執行上限 "
                f"{self.max_tokens_per_run}（目前已用 {self.used_this_run}），呼叫前即中止。"
            )

        response, actual_tokens_used = llm_call_fn(prompt_text)
        self.check_and_record(actual_tokens_used)
        return response
