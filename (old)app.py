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
            repeat TEXT DEFAULT 'none',
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

    # Migration: 如果舊的 reminders 表沒有 repeat 欄位，加上去
    try:
        c.execute("ALTER TABLE reminders ADD COLUMN repeat TEXT DEFAULT 'none'")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # 欄位已存在，忽略

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

def add_reminder(content, remind_at, repeat="none"):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO reminders (content, remind_at, repeat) VALUES (?, ?, ?)", (content, remind_at, repeat))
    rid = c.lastrowid
    conn.commit()
    conn.close()
    repeat_label = {"none": "", "daily": "（每天重複）", "weekly": "（每週重複）", "monthly": "（每月重複）"}
    msg = f"⏰ 已設定提醒 #{rid}：{content}\n🕐 將在 {remind_at} 提醒你{repeat_label.get(repeat, '')}"
    return msg


def list_reminders():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, content, remind_at, repeat FROM reminders WHERE sent=0 ORDER BY remind_at")
    rows = c.fetchall()
    conn.close()
    if not rows:
        return "目前沒有待提醒的事項。"
    repeat_icon = {"none": "", "daily": " 🔁每天", "weekly": " 🔁每週", "monthly": " 🔁每月"}
    lines = ["⏰ *提醒清單*\n"]
    for rid, content, remind_at, repeat in rows:
        lines.append(f"• `#{rid}` {content} — {remind_at}{repeat_icon.get(repeat, '')}")
    return "\n".join(lines)


def calc_next_remind_at(remind_at_str, repeat):
    """根據 repeat 類型計算下一次提醒時間"""
    dt = datetime.strptime(remind_at_str, "%Y-%m-%d %H:%M")
    if repeat == "daily":
        dt += timedelta(days=1)
    elif repeat == "weekly":
        dt += timedelta(weeks=1)
    elif repeat == "monthly":
        # 加一個月（簡單處理：加 30 天）
        dt += timedelta(days=30)
    else:
        return None
    return dt.strftime("%Y-%m-%d %H:%M")


def check_reminders():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, content, remind_at, repeat FROM reminders WHERE sent=0 AND remind_at<=?", (now,))
    rows = c.fetchall()

    results = []
    for rid, content, remind_at, repeat in rows:
        results.append((rid, content))

        if repeat and repeat != "none":
            # 重複提醒：算出下一次時間，建新的一筆
            next_at = calc_next_remind_at(remind_at, repeat)
            if next_at:
                c.execute("INSERT INTO reminders (content, remind_at, repeat) VALUES (?, ?, ?)", (content, next_at, repeat))
                logger.info(f"重複提醒 #{rid} 下一次：{next_at}")

        # 標記當前這筆為已發送
        c.execute("UPDATE reminders SET sent=1 WHERE id=?", (rid,))

    conn.commit()
    conn.close()
    return results


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
- 用台灣人聊天的口吻，像跟同事朋友講話
- 語氣隨性但可靠，像那種很罩的同事
- 會用語助詞：「啊」「啦」「喔」「欸」「蛤」
- 會用口語縮寫：「先這樣」「應該ok」「沒問題」「搞定」「收到」
- 偶爾用「哈哈」「XD」表示輕鬆
- 可以用注音文增加親切感：「ㄅ」「ㄇ」「ㄏㄏ」
- 適當使用 emoji，但不要每句都有
- 不要太正式、不要用「您」，就像 LINE 群組在聊天
- 如果用戶要你記住什麼事情，用待辦功能幫他記
- 覺得很疑惑時會只回傳一個 ?

語氣範例：
- 「收到～幫你記起來了 👍」
- 「欸這個明天到期喔，要注意一下」
- 「搞定！還有其他事ㄇ」
- 「歐虧，幫你設好提醒了啦」
- 「賀！收到」

主人的作息資訊（用來判斷模糊時間）：
- 上班時間：09:00
- 下班時間：18:00
- 「下班前」= 17:30（下班前半小時）
- 「中午」= 12:00
- 「早上」= 09:00
- 「下午」= 14:00
- 「傍晚」= 17:00
- 當用戶說模糊的時間（如「下班前」「明天早上」），請自動轉換成具體的 YYYY-MM-DD HH:MM 格式

工具使用規則：
- 當用戶說「記一下」「幫我記」「新增待辦」「to-do」→ 使用 add_todo
- 當用戶說「記一下...到時候提醒我」「記住...並提醒」等同時要記錄和提醒 → 使用 add_todo_with_reminder
- 當用戶只說「待辦」「列出待辦」「有什麼事」「我的任務」→ 使用 list_todos
- 當用戶說「提醒我」「remind」→ 使用 add_reminder（如果提到「每天」「每週」「每月」，設定對應的 repeat 參數）
- 當用戶說「完成」「做完了」→ 使用 complete_todo
- 當用戶說「刪除」「取消」→ 使用 delete_todo
- 其他情況 → 直接對話回答
"""

TOOLS = [
    {"type": "function", "function": {"name": "add_todo", "description": "新增一個待辦事項。當用戶要你記住某件事、或提到待辦事項時使用。", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "待辦事項內容"}, "due_date": {"type": "string", "description": "期限，格式 YYYY-MM-DD，可以為空"}}, "required": ["content"]}}},
    {"type": "function", "function": {"name": "list_todos", "description": "列出目前的待辦事項。當用戶問有什麼待辦、任務、或想看清單時使用。", "parameters": {"type": "object", "properties": {"include_completed": {"type": "boolean", "description": "是否包含已完成的項目", "default": False}}}}},
    {"type": "function", "function": {"name": "complete_todo", "description": "將一個待辦事項標記為完成。", "parameters": {"type": "object", "properties": {"todo_id": {"type": "integer", "description": "待辦事項的 ID 編號"}}, "required": ["todo_id"]}}},
    {"type": "function", "function": {"name": "delete_todo", "description": "刪除一個待辦事項。", "parameters": {"type": "object", "properties": {"todo_id": {"type": "integer", "description": "待辦事項的 ID 編號"}}, "required": ["todo_id"]}}},
    {"type": "function", "function": {"name": "add_reminder", "description": "設定一個定時提醒。支援一次性或重複提醒。", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "提醒內容"}, "remind_at": {"type": "string", "description": "第一次提醒時間，格式 YYYY-MM-DD HH:MM"}, "repeat": {"type": "string", "enum": ["none", "daily", "weekly", "monthly"], "description": "重複頻率：none=一次性, daily=每天, weekly=每週, monthly=每月", "default": "none"}}, "required": ["content", "remind_at"]}}},
    {"type": "function", "function": {"name": "list_reminders", "description": "列出目前待提醒的事項。", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "add_todo_with_reminder", "description": "同時新增待辦事項並設定提醒。當用戶想記錄一件事並且希望到時候被提醒時使用。", "parameters": {"type": "object", "properties": {"content": {"type": "string", "description": "待辦事項內容"}, "due_date": {"type": "string", "description": "期限，格式 YYYY-MM-DD"}, "remind_at": {"type": "string", "description": "提醒時間，格式 YYYY-MM-DD HH:MM"}}, "required": ["content", "due_date", "remind_at"]}}},
]

TOOL_FUNCTIONS = {
    "add_todo": lambda args: add_todo(args["content"], args.get("due_date")),
    "list_todos": lambda args: list_todos(args.get("include_completed", False)),
    "complete_todo": lambda args: complete_todo(args["todo_id"]),
    "delete_todo": lambda args: delete_todo(args["todo_id"]),
    "add_reminder": lambda args: add_reminder(args["content"], args["remind_at"], args.get("repeat", "none")),
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

# 關鍵字 → 圖片對應（提醒內容包含關鍵字就附圖）
# 圖片放在 images/ 目錄下
REMINDER_IMAGES = {
    "喝水": "images/drink_water.jpg",
    # 之後可以繼續加，例如：
    # "運動": "images/exercise.png",
    # "吃藥": "images/medicine.png",
}


def get_reminder_image(content):
    """根據提醒內容找對應的圖片路徑"""
    for keyword, image_path in REMINDER_IMAGES.items():
        if keyword in content:
            full_path = os.path.join(os.path.dirname(__file__), image_path)
            if os.path.exists(full_path):
                return full_path
    return None


def send_reminder(slack_app, content):
    """發送提醒，如果有對應圖片就一起發送"""
    if not MY_SLACK_USER_ID:
        return

    image_path = get_reminder_image(content)

    if image_path:
        # 有圖片：先取得 DM channel ID，再上傳圖片
        dm = slack_app.client.conversations_open(users=[MY_SLACK_USER_ID])
        dm_channel_id = dm["channel"]["id"]
        slack_app.client.files_upload_v2(
            channel=dm_channel_id,
            file=image_path,
            initial_comment=f"⏰ *提醒*：{content}",
        )
    else:
        # 沒圖片：純文字
        slack_app.client.chat_postMessage(
            channel=MY_SLACK_USER_ID,
            text=f"⏰ *提醒*：{content}",
        )


def background_scheduler(slack_app):
    logger.info("背景排程已啟動")
    last_summary_date = None
    last_eod_check_date = None

    while True:
        try:
            now = datetime.now()

            # --- 檢查提醒 ---
            for rid, content in check_reminders():
                if MY_SLACK_USER_ID:
                    try:
                        send_reminder(slack_app, content)
                        logger.info(f"已發送提醒 #{rid}")
                    except Exception as e:
                        logger.error(f"發送提醒失敗: {e}")

            # --- 每日摘要（早上）---
            if now.hour == DAILY_SUMMARY_HOUR and now.minute < 1 and last_summary_date != now.date() and MY_SLACK_USER_ID:
                try:
                    slack_app.client.chat_postMessage(channel=MY_SLACK_USER_ID, text=generate_daily_summary())
                    last_summary_date = now.date()
                    logger.info("已發送每日摘要")
                except Exception as e:
                    logger.error(f"發送每日摘要失敗: {e}")

            # --- 下班前待辦提醒（17:30）---
            if now.hour == 17 and 30 <= now.minute < 31 and last_eod_check_date != now.date() and MY_SLACK_USER_ID:
                try:
                    eod_msg = generate_eod_reminder()
                    if eod_msg:
                        slack_app.client.chat_postMessage(channel=MY_SLACK_USER_ID, text=eod_msg)
                        logger.info("已發送下班前待辦提醒")
                    last_eod_check_date = now.date()
                except Exception as e:
                    logger.error(f"發送下班前提醒失敗: {e}")

        except Exception as e:
            logger.error(f"排程錯誤: {e}")

        time.sleep(30)


def generate_eod_reminder():
    """產生下班前未完成待辦提醒，如果全部完成就回傳 None"""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, content, due_date FROM todos WHERE status='pending' ORDER BY due_date")
    todos = c.fetchall()
    conn.close()

    if not todos:
        return None

    lines = [f"🔔 *快下班了！你還有 {len(todos)} 項待辦未完成：*\n"]
    for tid, content, due_date in todos:
        line = f"⬜ `#{tid}` {content}"
        if due_date:
            line += f"  (📅 {due_date})"
        lines.append(line)
    lines.append("\n做完了跟我說 `完成 #編號`，或明天再處理也沒關係 💪")
    return "\n".join(lines)


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