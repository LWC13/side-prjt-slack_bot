# 🤖 Slack Agent Bot — 個人 AI 工作助理

一個跑在 Slack 裡的個人 AI 助理，幫你追蹤待辦、設定提醒、每天早上給你工作摘要。

## 功能

| 功能 | 說明 | 範例 |
|------|------|------|
| ✅ 待辦追蹤 | 記錄、列出、完成、刪除待辦 | `記一下 週五前要交評估報告` |
| ⏰ 提醒 | 指定時間提醒你 | `明天下午3點提醒我跟進 fine-tuning 結果` |
| ☀️ 每日摘要 | 每天早上自動發送今日待辦 | 自動執行 |
| 💬 LLM 問答 | 串接 Claude，可以問任何問題 | `幫我想一下明天 demo 要講什麼` |
| 🧠 對話記憶 | 記得最近 20 輪對話 | 自動運作 |

## 快速開始

### 1. 建立 Slack App

1. 到 [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. 取一個名字（例如 `我的助理`），選擇你的 Workspace

### 2. 設定 Slack App 權限

**Socket Mode：**
- Settings → Socket Mode → **Enable Socket Mode**
- 建立 App-Level Token（勾選 `connections:write`）→ 複製 `xapp-` 開頭的 token

**Bot Token Scopes（OAuth & Permissions）：**
- `app_mentions:read`
- `chat:write`
- `im:history`
- `im:read`
- `im:write`

**Event Subscriptions：**
- 開啟 Enable Events
- Subscribe to bot events：
  - `app_mention`
  - `message.im`

**Install App：**
- Install to Workspace → 複製 `xoxb-` 開頭的 Bot Token

### 3. 設定環境變數

```bash
cp .env.example .env
# 編輯 .env，填入你的 token
```

### 4. 安裝與執行

```bash
pip install -r requirements.txt

# 載入環境變數（Linux/Mac）
export $(cat .env | grep -v '^#' | xargs)

# 啟動
python app.py
```

### 5. 開始使用

在 Slack 裡：
- **@你的Bot** `記一下 週五前交報告` → 新增待辦
- **@你的Bot** `待辦` → 列出所有待辦
- **@你的Bot** `完成 #1` → 完成第 1 項
- **@你的Bot** `明天下午3點提醒我開會` → 設定提醒
- **@你的Bot** `幫我想一下簡報大綱` → 直接跟 Claude 聊

也可以直接 **DM（私訊）** 你的 Bot，不需要 @ 就能對話。

## 架構

```
Slack (你的訊息)
  → Socket Mode 接收
    → LLM (Claude) 判斷意圖 + Tool Use
      → 待辦 / 提醒 / 對話（SQLite）
    → 回傳結果到 Slack

背景排程（每 30 秒）
  → 檢查到期提醒 → 發送 Slack DM
  → 每日早上 → 發送今日摘要
```

## 檔案結構

```
slack-agent-bot/
├── app.py           # 主程式（所有邏輯都在這裡）
├── agent.db         # SQLite 資料庫（自動建立）
├── requirements.txt # Python 依賴
├── .env.example     # 環境變數範本
└── README.md        # 你正在看的這個
```

## 之後可以加的功能

- [ ] 串接 Google Calendar
- [ ] 串接公司內部 API（HPC 狀態、模型評估結果）
- [ ] 定期自動跑評估腳本，結果推送到 Slack
- [ ] 用 Docker 容器化部署
- [ ] 加入更多 tool（搜尋、檔案操作）
- [ ] Web UI 管理介面

## 技術細節

- **通訊**：Slack Bolt (Socket Mode) — 不需要公開 URL
- **LLM**：Anthropic Claude API (Tool Use)
- **資料庫**：SQLite — 輕量、零設定
- **排程**：Python Thread — 簡單但夠用
- **記憶**：最近 20 輪對話存在 SQLite
