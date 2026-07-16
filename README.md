# JIRA Real-Time Loader Agent

自動化 JIRA Case 簽核成員確認工具，取代 RD 手動查詢 PS 的流程。

## 核心邏輯

1. **Real-time** 讀取 SharePoint「產品生命週期文件流簽表」（PDP文件流簽 分頁），
   比對表單名稱「程式類承認書」(HQ-PD.156)，取得官方角色代碼對應規則。
2. 解析 JIRA Ticket，取得 M_PDM Project Number。
3. **邏輯1（主要）**：即時登入 PDM(Windchill)，依專案號碼查詢角色成員。
4. **邏輯2（過渡期備援）**：JIRA ticket 內建簽核表（若存在；未來會取消）。
5. 邏輯1/邏輯2 結果比對：
   - 只有一邊有值 → 自動採用
   - 兩邊一致 → 自動採用
   - 兩邊不一致（衝突）→ 標記 `[待選擇]`，JIRA留言列出選項，由 RD 文字回覆決定
   - 兩邊皆無 → 標記 `[待確認]`，JIRA留言通知人工確認
6. 產出 `loader.txt` 給 RD。

## 專案結構

```
config/             # 設定檔（token預算、角色對應規則）
src/auth/           # Admin Gate（密碼/API Key/PDM帳密治理）
src/loaders/        # PDM / JIRA / SharePoint 資料讀取
src/logic/          # 欄位解析核心邏輯（邏輯1/邏輯2比對、衝突判斷）
src/output/         # loader.txt 輸出
src/utils/          # 共用工具（命名正規化）
src/token_guard.py  # Token 預算 hard limit 控管
src/jira_notifier.py # JIRA留言通知
tests/              # 測試與 mock 資料
```

## 環境設定

```bash
cp .env.example .env
pip install -r requirements.txt
playwright install chromium
```

`.env` 內的 API Key 請勿填入真實值，系統會在需要時提示管理員手動輸入。

## 已知限制 / 待驗證項目

- `src/loaders/pdm_reader.py` 中的 `login()` / `search_project()` selector
  尚未在真實 PDM 環境測試，需依實際 DOM 結構調整。
- SharePoint 文件的 real-time 讀取模組（`sharepoint_reader.py`）尚未實作。
- JIRA ticket 讀取模組（`jira_reader.py`）尚未實作。
