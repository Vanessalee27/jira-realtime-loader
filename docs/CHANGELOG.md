# Changelog

## [Unreleased]

### Added
- Admin Gate：管理員密碼驗證、變更密碼、API Key/PDM帳密手動輸入、模型選擇
- Token Guard：單次執行 / 每日累計 token hard limit 控管
- naming.py：統一命名正規化規則（PDM成員格式 / loader.txt / JIRA mention 共用）
- fallback_resolver.py：邏輯1(PDM)與邏輯2(JIRA)欄位級比對，含衝突偵測
- loader_writer.py：loader.txt 輸出，支援 [待選擇] / [待確認] 標記
- jira_notifier.py：JIRA留言（初次結果 + RD回覆解析 + 最終公告），每次發新留言不編輯
- pdm_reader.py：PDM(Windchill) 自動登入爬蟲骨架，表格解析邏輯已用真實截圖資料驗證
- role_mapping.yaml：依官方文件流簽表 HQ-PD.156「程式類承認書」定案角色對應規則

### Decisions
- 取消邏輯3（產品樹對應維護代表），改為「邏輯1/邏輯2結果衝突時交由RD人工選擇」
- 忽略 HQ-PD.156 附註的 SW STM UM 替代規則
- 欄位級 fallback（非整組失敗）
- loader.txt 缺值/衝突欄位照樣產出，不整份暫緩
- RD 回覆選擇機制：手動觸發重跑（不做自動輪詢）
- 每次執行都發新的 JIRA 留言，不編輯既有留言

### Known Limitations
- pdm_reader.py 的登入/搜尋流程未經真實環境驗證
- sharepoint_reader.py / jira_reader.py 尚未實作
