"""
Slack Agent Bot - 個人 AI 助理
功能：
1. 待辦追蹤（記住任務、列出待辦、完成任務）
2. 提醒（定時提醒）
3. 每日摘要（每天早上自動發送）
4. LLM 問答（串接 OpenAI API）
"""

import os
import json
import sqlite3
import logging
from datetime import datetime, timedelta
from threading import Thread
import time
import re

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from openai import OpenAI

# ============================================================
# 設定
# ============================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Slack tokens（從環境變數讀取）
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN")

# OpenAI API key
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# 模型選擇（可換成 gpt-4o-mini 省錢）
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")

# 你的 Slack User ID（用來發送每日摘要和提醒）
MY_SLACK_USER_ID = os.environ.get("MY_SLACK_USER_ID", "")

# 每日摘要時間（24 小時制，預設早上 9 點）
DAILY_SUMMARY_HOUR = int(os.environ.get("DAILY_SUMMARY_HOUR", "9"))

# ============================================================
# 資料庫
# ============================================================

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "agent.db"))


def init_db():
    """初始化 SQLite 資料庫"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 待辦事項
    c.execute("""
        CREATE TABLE IF NOT EXISTS todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            due_date TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            completed_at TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)

    # 提醒
    c.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            remind_at TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            sent INTEGER DEFAULT 0
        )
    """)

    # 對話記憶（最近的對話歷史）
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    conn.commit()
    conn.close()


def get_db():
    return sqlite3.connect(DB_PATH)


# ============================================================
# 待辦事項功能
# ============================================================

def add_todo(content: str, due_date: str = None) -> str:
    """新增待辦事項"""
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO todos (content, due_date) VALUES (?, ?)", (content, due_date))
    todo_id = c.lastrowid
    conn.commit()
    conn.close()

    msg = f"✅ 已記錄待辦 #{todo_id}：{content}"
    if due_date:
        msg += f"\n📅 期限：{due_date}"
    return msg


def list_todos(include_completed: bool = False) -> str:
    """列出待辦事項"""
    conn = get_db()
    c = conn.cursor()

    if include_completed:
        c.execute("SELECT id, content, due_date, status, created_at FROM todos ORDER BY created_at DESC")
    else:
        c.execute("SELECT id, content, due_date, status, created_at FROM todos WHERE status = 'pending' ORDER BY created_at DESC")

    rows = c.fetchall()
    conn.close()

    if not rows:
        return "🎉 目前沒有待辦事項！"

    lines = ["📋 *待辦事項清單*\n"]
    for row in rows:
        tid, content, due_date, status, created_at = row
        icon = "⬜" if status == "pending" else "✅"
        line = f"{icon} `#{tid}` {content}"
        if due_date:
            line += f"  (📅 {due_date})"
        lines.append(line)

    return "\n".join(lines)


def complete_todo(todo_id: int) -> str:
    """完成待辦事項"""
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "UPDATE todos SET status = 'done', completed_at = datetime('now', 'localtime') WHERE id = ?",
        (todo_id,),
    )
    if c.rowcount == 0:
        conn.close()
        return f"❌ 找不到待辦 #{todo_id}"
    conn.commit()
    conn.close()
    return f"✅ 已完成待辦 #{todo_id}！"


def delete_todo(todo_id: int) -> str:
    """刪除待辦事項"""
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
    if c.rowcount == 0:
        conn.close()
        return f"❌ 找不到待辦 #{todo_id}"
    conn.commit()
    conn.close()
    return f"🗑️ 已刪除待辦 #{todo_id}"


# ============================================================
# 提醒功能
# ============================================================

def add_reminder(content: str, remind_at: str) -> str:
    """新增提醒"""
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO reminders (content, remind_at) VALUES (?, ?)", (content, remind_at))
    rid = c.lastrowid
    conn.commit()
    conn.close()
    return f"⏰ 已設定提醒 #{rid}：{content}\n🕐 將在 {remind_at} 提醒你"


def list_reminders() -> str:
    """列出未發送的提醒"""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, content, remind_at FROM reminders WHERE sent = 0 ORDER BY remind_at")
    rows = c.fetchall()
    conn.close()

    if not rows:
        return "目前沒有待提醒的事項。"

    lines = ["⏰ *提醒清單*\n"]
    for row in rows:
        rid, content, remind_at = row
        lines.append(f"• `#{rid}` {content} — {remind_at}")

    return "\n".join(lines)


def check_reminders() -> list:
    """檢查是否有到期的提醒"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, content FROM reminders WHERE sent = 0 AND remind_at <= ?", (now,))
    rows = c.fetchall()

    for row in rows:
        c.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (row[0],))

    conn.commit()
    conn.close()
    return rows


# ============================================================
# 對話記憶
# ============================================================

def save_chat(role: str, content: str):
    """儲存對話"""
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO chat_history (role, content) VALUES (?, ?)", (role, content))
    conn.commit()
    conn.close()


def get_recent_chat(limit: int = 20) -> list:
    """取得最近的對話歷史"""
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "SELECT role, content FROM chat_history ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = c.fetchall()
    conn.close()
    # 反轉，讓最舊的在前面
    return [{"role": r, "content": c} for r, c in reversed(rows)]


# ============================================================
# LLM 整合
# ============================================================

SYSTEM_PROMPT = """你是一個個人工作助理 Bot，住在 Slack 裡面。你的主人是一位在金融控股公司做 AI/ML 的 PM/DS/MLE。

你的能力：
1. 管理待辦事項（新增、列出、完成、刪除）
2. 設定提醒
3. 回答問題、提供建議
4. 幫忙整理想法和筆記

回應風格：
- 簡潔直接，不廢話
- 用繁體中文
- 適當使用 emoji
- 如果用戶要你記住什麼事情，用待辦功能幫他記

你可以使用以下工具，根據用戶的訊息判斷是否需要呼叫：

工具使用規則：
- 當用戶說「記一下」「幫我記」「待辦」「to-do」→ 使用 add_todo
- 當用戶說「提醒我」「remind」→ 使用 add_reminder  
- 當用戶說「列出待辦」「有什麼事」「我的任務」→ 使用 list_todos
- 當用戶說「完成」「做完了」→ 使用 complete_todo
- 當用戶說「刪除」「取消」→ 使用 delete_todo
- 其他情況 → 直接對話回答
"""

# 定義給 OpenAI 的 tools（function calling 格式）
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_todo",
            "description": "新增一個待辦事項。當用戶要你記住某件事、或提到待辦事項時使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "待辦事項內容",
                    },
                    "due_date": {
                        "type": "string",
                        "description": "期限，格式 YYYY-MM-DD，可以為空",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_todos",
            "description": "列出目前的待辦事項。",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_completed": {
                        "type": "boolean",
                        "description": "是否包含已完成的項目",
                        "default": False,
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_todo",
            "description": "將一個待辦事項標記為完成。",
            "parameters": {
                "type": "object",
                "properties": {
                    "todo_id": {
                        "type": "integer",
                        "description": "待辦事項的 ID 編號",
                    },
                },
                "required": ["todo_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_todo",
            "description": "刪除一個待辦事項。",
            "parameters": {
                "type": "object",
                "properties": {
                    "todo_id": {
                        "type": "integer",
                        "description": "待辦事項的 ID 編號",
                    },
                },
                "required": ["todo_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_reminder",
            "description": "設定一個定時提醒。",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "提醒內容",
                    },
                    "remind_at": {
                        "type": "string",
                        "description": "提醒時間，格式 YYYY-MM-DD HH:MM",
                    },
                },
                "required": ["content", "remind_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": "列出目前待提醒的事項。",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]

# Tool 名稱對應到實際函數
TOOL_FUNCTIONS = {
    "add_todo": lambda args: add_todo(args["content"], args.get("due_date")),
    "list_todos": lambda args: list_todos(args.get("include_completed", False)),
    "complete_todo": lambda args: complete_todo(args["todo_id"]),
    "delete_todo": lambda args: delete_todo(args["todo_id"]),
    "add_reminder": lambda args: add_reminder(args["content"], args["remind_at"]),
    "list_reminders": lambda args: list_reminders(),
}


def chat_with_llm(user_message: str) -> str:
    """與 LLM 對話，支援 tool use（OpenAI function calling）"""
    if not OPENAI_API_KEY:
        return "⚠️ 未設定 OPENAI_API_KEY，LLM 功能無法使用。\n你可以直接用指令操作：`記一下 <內容>` / `待辦` / `完成 #<id>`"

    client = OpenAI(api_key=OPENAI_API_KEY)

    # 取得對話歷史
    history = get_recent_chat(limit=20)

    # 加入今天的日期作為上下文
    today = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
    system_msg = {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n現在時間：{today}"}

    # 組合訊息
    messages = [system_msg] + history + [{"role": "user", "content": user_message}]

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            max_tokens=1024,
            tools=TOOLS,
            messages=messages,
        )

        message = response.choices[0].message

        # 處理 tool use 迴圈
        while message.tool_calls:
            # 把 assistant 的回覆加進對話
            messages.append(message)

            # 執行每個 tool call
            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                tool_input = json.loads(tool_call.function.arguments)
                logger.info(f"Tool call: {tool_name}({tool_input})")

                if tool_name in TOOL_FUNCTIONS:
                    result = TOOL_FUNCTIONS[tool_name](tool_input)
                else:
                    result = f"未知工具：{tool_name}"

                # 把 tool 結果加進對話
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })

            # 讓 LLM 根據 tool 結果繼續回覆
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                max_tokens=1024,
                tools=TOOLS,
                messages=messages,
            )
            message = response.choices[0].message

        # 取得最終回覆
        reply = message.content or ""

        # 儲存對話
        save_chat("user", user_message)
        save_chat("assistant", reply)

        return reply

    except Exception as e:
        logger.error(f"LLM error: {e}")
        return f"⚠️ LLM 呼叫失敗：{str(e)}"


# ============================================================
# Slack App
# ============================================================

app = App(token=SLACK_BOT_TOKEN)


@app.event("app_mention")
def handle_mention(event, say):
    """當有人 @bot 時觸發"""
    text = event.get("text", "")
    # 移除 @bot 的 mention 標記
    text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()

    if not text:
        say("嗨！有什麼我可以幫你的嗎？試試看：\n• `記一下 <內容>` — 新增待辦\n• `待辦` — 列出待辦\n• `提醒我 <時間> <內容>` — 設定提醒")
        return

    # 透過 LLM 處理
    reply = chat_with_llm(text)
    say(reply)


@app.event("message")
def handle_dm(event, say):
    """處理 DM 訊息"""
    # 只處理 DM（im 類型），忽略 bot 自己的訊息
    if event.get("channel_type") != "im":
        return
    if event.get("bot_id"):
        return

    text = event.get("text", "").strip()
    if not text:
        return

    reply = chat_with_llm(text)
    say(reply)


# ============================================================
# 背景排程（提醒 + 每日摘要）
# ============================================================

def background_scheduler(slack_app: App):
    """背景執行緒：每分鐘檢查提醒、每天早上發送摘要"""
    logger.info("背景排程已啟動")
    last_summary_date = None

    while True:
        try:
            now = datetime.now()

            # --- 檢查提醒 ---
            due_reminders = check_reminders()
            for rid, content in due_reminders:
                if MY_SLACK_USER_ID:
                    try:
                        slack_app.client.chat_postMessage(
                            channel=MY_SLACK_USER_ID,
                            text=f"⏰ *提醒*：{content}",
                        )
                        logger.info(f"已發送提醒 #{rid}")
                    except Exception as e:
                        logger.error(f"發送提醒失敗: {e}")

            # --- 每日摘要 ---
            if (
                now.hour == DAILY_SUMMARY_HOUR
                and now.minute < 1
                and last_summary_date != now.date()
                and MY_SLACK_USER_ID
            ):
                summary = generate_daily_summary()
                try:
                    slack_app.client.chat_postMessage(
                        channel=MY_SLACK_USER_ID,
                        text=summary,
                    )
                    last_summary_date = now.date()
                    logger.info("已發送每日摘要")
                except Exception as e:
                    logger.error(f"發送每日摘要失敗: {e}")

        except Exception as e:
            logger.error(f"排程錯誤: {e}")

        time.sleep(30)  # 每 30 秒檢查一次


def generate_daily_summary() -> str:
    """產生每日摘要"""
    today = datetime.now().strftime("%Y-%m-%d")
    weekday = datetime.now().strftime("%A")

    conn = get_db()
    c = conn.cursor()

    # 待辦事項
    c.execute("SELECT id, content, due_date FROM todos WHERE status = 'pending' ORDER BY due_date")
    todos = c.fetchall()

    # 今天到期的
    c.execute(
        "SELECT id, content FROM todos WHERE status = 'pending' AND due_date = ?",
        (today,),
    )
    due_today = c.fetchall()

    # 今天的提醒
    c.execute(
        "SELECT content, remind_at FROM reminders WHERE sent = 0 AND remind_at LIKE ?",
        (f"{today}%",),
    )
    today_reminders = c.fetchall()

    conn.close()

    lines = [f"☀️ *早安！今天是 {today} ({weekday})*\n"]

    if due_today:
        lines.append("🔴 *今天到期：*")
        for tid, content in due_today:
            lines.append(f"  • `#{tid}` {content}")
        lines.append("")

    if today_reminders:
        lines.append("⏰ *今天的提醒：*")
        for content, remind_at in today_reminders:
            time_part = remind_at.split(" ")[1] if " " in remind_at else remind_at
            lines.append(f"  • {time_part} — {content}")
        lines.append("")

    if todos:
        lines.append(f"📋 *所有待辦 ({len(todos)} 項)：*")
        for tid, content, due_date in todos[:10]:  # 最多顯示 10 項
            line = f"  • `#{tid}` {content}"
            if due_date:
                line += f"  (📅 {due_date})"
            lines.append(line)
        if len(todos) > 10:
            lines.append(f"  ...還有 {len(todos) - 10} 項")
    else:
        lines.append("🎉 目前沒有待辦事項，今天輕鬆！")

    lines.append("\n_回覆我任何訊息就可以開始工作 💪_")

    return "\n".join(lines)


# ============================================================
# 啟動
# ============================================================

def main():
    init_db()
    logger.info("資料庫初始化完成")

    # 啟動背景排程
    scheduler_thread = Thread(target=background_scheduler, args=(app,), daemon=True)
    scheduler_thread.start()

    # 啟動 Slack Bot（Socket Mode）
    logger.info("Slack Bot 啟動中...")
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()


if __name__ == "__main__":
    main()
