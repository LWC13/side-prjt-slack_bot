"""
Slack Agent Bot - 個人 AI 助理
功能：
1. 待辦追蹤（記住任務、列出待辦、完成任務）
2. 提醒（定時提醒）
3. 每日摘要（每天早上自動發送）
4. LLM 問答（串接 OpenAI API）
5. 圖片分析（串接 Gemini API）
6. /command 指令系統（模組化擴充）
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
from vision import process_slack_image, EXPENSE_EXTRACT_PROMPT

# ============================================================
# 設定
# ============================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
MY_SLACK_USER_ID = os.environ.get("MY_SLACK_USER_ID", "")
DAILY_SUMMARY_HOUR = int(os.environ.get("DAILY_SUMMARY_HOUR", "9"))
IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}

# ============================================================
# 資料庫
# ============================================================

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "agent.db"))


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL, due_date TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            completed_at TEXT, status TEXT DEFAULT 'pending'
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL, remind_at TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            sent INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL, content TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    conn.commit()
    conn.close()


def get_db():
    return sqlite3.connect(DB_PATH)


# ============================================================
# 待辦事項
# ============================================================

def add_todo(content, due_date=None):
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


def list_todos(include_completed=False):
    conn = get_db()
    c = conn.cursor()
    if include_completed:
        c.execute("SELECT id, content, due_date, status FROM todos ORDER BY created_at DESC")
    else:
        c.execute("SELECT id, content, due_date, status FROM todos WHERE status = 'pending' ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    if not rows:
        return "🎉 目前沒有待辦事項！"
    lines = ["📋 *待辦事項清單*\n"]
    for tid, content, due_date, status in rows:
        icon = "⬜" if status == "pending" else "✅"
        line = f"{icon} `#{tid}` {content}"
        if due_date:
            line += f"  (📅 {due_date})"
        lines.append(line)
    return "\n".join(lines)


def complete_todo(todo_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE todos SET status='done', completed_at=datetime('now','localtime') WHERE id=?", (todo_id,))
    if c.rowcount == 0:
        conn.close()
        return f"❌ 找不到待辦 #{todo_id}"
    conn.commit()
    conn.close()
    return f"✅ 已完成待辦 #{todo_id}！"


def delete_todo(todo_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM todos WHERE id=?", (todo_id,))
    if c.rowcount == 0:
        conn.close()
        return f"❌ 找不到待辦 #{todo_id}"
    conn.commit()
    conn.close()
    return f"🗑️ 已刪除待辦 #{todo_id}"


# ============================================================
# 提醒
# ============================================================

def add_reminder(content, remind_at):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO reminders (content, remind_at) VALUES (?, ?)", (content, remind_at))
    rid = c.lastrowid
    conn.commit()
    conn.close()
    return f"⏰ 已設定提醒 #{rid}：{content}\n🕐 將在 {remind_at} 提醒你"


def list_reminders():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, content, remind_at FROM reminders WHERE sent=0 ORDER BY remind_at")
    rows = c.fetchall()
    conn.close()
    if not rows:
        return "目前沒有待提醒的事項。"
    lines = ["⏰ *提醒清單*\n"]
    for rid, content, remind_at in rows:
        lines.append(f"• `#{rid}` {content} — {remind_at}")
    return "\n".join(lines)


def check_reminders():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, content FROM reminders WHERE sent=0 AND remind_at<=?", (now,))
    rows = c.fetchall()
    for row in rows:
        c.execute("UPDATE reminders SET sent=1 WHERE id=?", (row[0],))
    conn.commit()
    conn.close()
    return rows


def add_todo_with_reminder(content, due_date, remind_at):
    """同時新增待辦事項和提醒"""
    todo_result = add_todo(content, due_date)
    reminder_result = add_reminder(content, remind_at)
    return f"{todo_result}\n{reminder_result}"


# ============================================================
# 對話記憶
# ============================================================

def save_chat(role, content):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO chat_history (role, content) VALUES (?, ?)", (role, content))
    conn.commit()
    conn.close()


def get_recent_chat(limit=20):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT role, content FROM chat_history ORDER BY id DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
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

工具使用規則：
- 當用戶說「記一下」「幫我記」「新增待辦」「to-do」→ 使用 add_todo
- 當用戶說「記一下...到時候提醒我」「記住...並提醒」等同時要記錄和提醒 → 使用 add_todo_with_reminder
- 當用戶只說「待辦」「列出待辦」「有什麼事」「我的任務」→ 使用 list_todos
- 當用戶說「提醒我」「remind」→ 使用 add_reminder
- 當用戶說「完成」「做完了」→ 使用 complete_todo
- 當用戶說「刪除」「取消」→ 使用 delete_todo
- 其他情況 → 直接對話回答
"""

TOOLS = [
    {"type": "function", "function": {"name": "add_todo", "description": "新增一個待辦事項。當用戶要你記住某件事、或提到待辦事項時使用。", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "待辦事項內容"}, "due_date": {"type": "string", "description": "期限，格式 YYYY-MM-DD，可以為空"}}, "required": ["content"]}}},
    {"type": "function", "function": {"name": "list_todos", "description": "列出目前的待辦事項。當用戶問有什麼待辦、任務、或想看清單時使用。", "parameters": {"type": "object", "properties": {"include_completed": {"type": "boolean", "description": "是否包含已完成的項目", "default": False}}}}},
    {"type": "function", "function": {"name": "complete_todo", "description": "將一個待辦事項標記為完成。", "parameters": {"type": "object", "properties": {"todo_id": {"type": "integer", "description": "待辦事項的 ID 編號"}}, "required": ["todo_id"]}}},
    {"type": "function", "function": {"name": "delete_todo", "description": "刪除一個待辦事項。", "parameters": {"type": "object", "properties": {"todo_id": {"type": "integer", "description": "待辦事項的 ID 編號"}}, "required": ["todo_id"]}}},
    {"type": "function", "function": {"name": "add_reminder", "description": "設定一個定時提醒。", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "提醒內容"}, "remind_at": {"type": "string", "description": "提醒時間，格式 YYYY-MM-DD HH:MM"}}, "required": ["content", "remind_at"]}}},
    {"type": "function", "function": {"name": "list_reminders", "description": "列出目前待提醒的事項。", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "add_todo_with_reminder", "description": "同時新增待辦事項並設定提醒。當用戶想記錄一件事並且希望到時候被提醒時使用。", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "待辦事項內容"}, "due_date": {"type": "string", "description": "期限，格式 YYYY-MM-DD"}, "remind_at": {"type": "string", "description": "提醒時間，格式 YYYY-MM-DD HH:MM"}}, "required": ["content", "due_date", "remind_at"]}}},
]

TOOL_FUNCTIONS = {
    "add_todo": lambda args: add_todo(args["content"], args.get("due_date")),
    "list_todos": lambda args: list_todos(args.get("include_completed", False)),
    "complete_todo": lambda args: complete_todo(args["todo_id"]),
    "delete_todo": lambda args: delete_todo(args["todo_id"]),
    "add_reminder": lambda args: add_reminder(args["content"], args["remind_at"]),
    "list_reminders": lambda args: list_reminders(),
    "add_todo_with_reminder": lambda args: add_todo_with_reminder(args["content"], args["due_date"], args["remind_at"]),
}


def chat_with_llm(user_message):
    if not OPENAI_API_KEY:
        return "⚠️ 未設定 OPENAI_API_KEY，LLM 功能無法使用。"
    client = OpenAI(api_key=OPENAI_API_KEY)
    history = get_recent_chat(limit=20)
    today = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
    messages = [{"role": "system", "content": f"{SYSTEM_PROMPT}\n\n現在時間：{today}"}] + history + [{"role": "user", "content": user_message}]

    try:
        response = client.chat.completions.create(model=OPENAI_MODEL, max_tokens=1024, tools=TOOLS, messages=messages)
        message = response.choices[0].message

        while message.tool_calls:
            messages.append(message)
            for tc in message.tool_calls:
                name = tc.function.name
                args = json.loads(tc.function.arguments)
                logger.info(f"Tool call: {name}({args})")
                result = TOOL_FUNCTIONS.get(name, lambda _: f"未知工具：{name}")(args)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
            response = client.chat.completions.create(model=OPENAI_MODEL, max_tokens=1024, tools=TOOLS, messages=messages)
            message = response.choices[0].message

        reply = message.content or ""
        save_chat("user", user_message)
        save_chat("assistant", reply)
        return reply
    except Exception as e:
        logger.error(f"LLM error: {e}")
        return f"⚠️ LLM 呼叫失敗：{str(e)}"


# ============================================================
# 圖片處理
# ============================================================

def handle_image_files(event, say, user_text=""):
    files = event.get("files", [])
    image_files = [f for f in files if f.get("mimetype", "") in IMAGE_MIME_TYPES]
    if not image_files:
        return False
    prompt = user_text if user_text else None
    for f in image_files:
        file_url = f.get("url_private", "")
        if not file_url:
            continue
        try:
            text, usage = process_slack_image(file_url, SLACK_BOT_TOKEN, prompt=prompt)
            say(text)
        except Exception as e:
            logger.error(f"圖片分析失敗: {e}")
            say(f"⚠️ 圖片分析失敗：{str(e)}")
    return True


# ============================================================
# /Command 指令系統
# ============================================================

COMMANDS = {}


def command(name, description, needs_image=False):
    """裝飾器：註冊一個 /command"""
    def decorator(func):
        COMMANDS[name] = {"handler": func, "description": description, "needs_image": needs_image}
        return func
    return decorator


# --- 指令定義（加新指令只要加一個 @command 函數）---

@command("/reimbursement", "🧾 報帳分析（需附圖片）", needs_image=True)
def cmd_reimbursement(event, say, args):
    if handle_image_files(event, say, user_text=EXPENSE_EXTRACT_PROMPT):
        return
    say("⚠️ 請附上收據或發票的圖片，再使用 `/reimbursement`")


@command("/summarize", "📝 摘要文字內容")
def cmd_summarize(event, say, args):
    if not args:
        say("⚠️ 請提供要摘要的內容，例如：`/summarize 一段很長的文字...`")
        return
    say(chat_with_llm(f"請幫我摘要以下內容，用繁體中文回答：\n{args}"))


@command("/translate", "🌐 翻譯成英文")
def cmd_translate(event, say, args):
    if not args:
        say("⚠️ 請提供要翻譯的內容，例如：`/translate 今天天氣很好`")
        return
    say(chat_with_llm(f"請將以下內容翻譯成英文，只輸出翻譯結果：\n{args}"))


@command("/status", "📊 顯示 Bot 狀態")
def cmd_status(event, say, args):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM todos WHERE status='pending'")
    pending = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM todos WHERE status='done'")
    done = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM reminders WHERE sent=0")
    reminders = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM chat_history")
    chats = c.fetchone()[0]
    conn.close()
    say(f"📊 *Bot 狀態*\n• 待辦：{pending} 項（已完成 {done} 項）\n• 待提醒：{reminders} 項\n• 對話紀錄：{chats} 則\n• 模型：`{OPENAI_MODEL}`\n• 時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}")


@command("/clear", "🗑️ 清除對話記憶")
def cmd_clear(event, say, args):
    conn = get_db()
    conn.cursor().execute("DELETE FROM chat_history")
    conn.commit()
    conn.close()
    say("🗑️ 已清除所有對話記憶，重新開始！")


@command("/help", "📖 顯示可用指令")
def cmd_help(event, say, args):
    lines = ["📖 *可用指令*\n"]
    for name, info in COMMANDS.items():
        lines.append(f"• `{name}` — {info['description']}")
    lines.append("\n💬 也可以直接打字跟我聊天，不需要指令！")
    say("\n".join(lines))


# ============================================================
# Slack App
# ============================================================

app = App(token=SLACK_BOT_TOKEN)


def process_message(event, say):
    """統一處理訊息的核心邏輯（@mention 和 DM 共用）"""
    text = event.get("text", "")
    text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()

    # 1. /command 指令
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd_name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        if cmd_name in COMMANDS:
            COMMANDS[cmd_name]["handler"](event, say, args)
        else:
            say(f"⚠️ 不認識的指令 `{cmd_name}`，打 `/help` 看可用指令")
        return

    # 2. 圖片 → Gemini 分析
    if handle_image_files(event, say, user_text=text):
        return

    # 3. 空訊息 → 歡迎
    if not text:
        say("嗨！有什麼我可以幫你的嗎？打 `/help` 看可用指令 😊")
        return

    # 4. 一般文字 → LLM 對話
    say(chat_with_llm(text))


@app.event("app_mention")
def handle_mention(event, say):
    process_message(event, say)


@app.event("message")
def handle_dm(event, say):
    if event.get("channel_type") != "im":
        return
    if event.get("bot_id"):
        return
    process_message(event, say)


# ============================================================
# 背景排程
# ============================================================

def background_scheduler(slack_app):
    logger.info("背景排程已啟動")
    last_summary_date = None

    while True:
        try:
            now = datetime.now()

            for rid, content in check_reminders():
                if MY_SLACK_USER_ID:
                    try:
                        slack_app.client.chat_postMessage(channel=MY_SLACK_USER_ID, text=f"⏰ *提醒*：{content}")
                        logger.info(f"已發送提醒 #{rid}")
                    except Exception as e:
                        logger.error(f"發送提醒失敗: {e}")

            if now.hour == DAILY_SUMMARY_HOUR and now.minute < 1 and last_summary_date != now.date() and MY_SLACK_USER_ID:
                try:
                    slack_app.client.chat_postMessage(channel=MY_SLACK_USER_ID, text=generate_daily_summary())
                    last_summary_date = now.date()
                    logger.info("已發送每日摘要")
                except Exception as e:
                    logger.error(f"發送每日摘要失敗: {e}")

        except Exception as e:
            logger.error(f"排程錯誤: {e}")

        time.sleep(30)


def generate_daily_summary():
    today = datetime.now().strftime("%Y-%m-%d")
    weekday = datetime.now().strftime("%A")
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, content, due_date FROM todos WHERE status='pending' ORDER BY due_date")
    todos = c.fetchall()
    c.execute("SELECT id, content FROM todos WHERE status='pending' AND due_date=?", (today,))
    due_today = c.fetchall()
    c.execute("SELECT content, remind_at FROM reminders WHERE sent=0 AND remind_at LIKE ?", (f"{today}%",))
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
        for tid, content, due_date in todos[:10]:
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
    logger.info(f"已註冊 {len(COMMANDS)} 個指令：{', '.join(COMMANDS.keys())}")
    Thread(target=background_scheduler, args=(app,), daemon=True).start()
    logger.info("Slack Bot 啟動中...")
    SocketModeHandler(app, SLACK_APP_TOKEN).start()


if __name__ == "__main__":
    main()