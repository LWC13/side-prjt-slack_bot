"""資料庫操作 - 待辦、提醒、對話記憶"""

import sqlite3
from datetime import datetime, timedelta
from config import DB_PATH, logger


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

    # Migration: 舊表加 repeat 欄位
    try:
        c.execute("ALTER TABLE reminders ADD COLUMN repeat TEXT DEFAULT 'none'")
        conn.commit()
    except sqlite3.OperationalError:
        pass

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
        c.execute("SELECT id, content, due_date, status FROM todos WHERE status='pending' ORDER BY created_at DESC")
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
    return f"⏰ 已設定提醒 #{rid}：{content}\n🕐 將在 {remind_at} 提醒你{repeat_label.get(repeat, '')}"


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
    dt = datetime.strptime(remind_at_str, "%Y-%m-%d %H:%M")
    if repeat == "daily":
        dt += timedelta(days=1)
    elif repeat == "weekly":
        dt += timedelta(weeks=1)
    elif repeat == "monthly":
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
            next_at = calc_next_remind_at(remind_at, repeat)
            if next_at:
                c.execute("INSERT INTO reminders (content, remind_at, repeat) VALUES (?, ?, ?)", (content, next_at, repeat))
                logger.info(f"重複提醒 #{rid} 下一次：{next_at}")
        c.execute("UPDATE reminders SET sent=1 WHERE id=?", (rid,))

    conn.commit()
    conn.close()
    return results


# ============================================================
# 複合操作
# ============================================================

def add_todo_with_reminder(content, due_date, remind_at):
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


def clear_chat_history():
    conn = get_db()
    conn.cursor().execute("DELETE FROM chat_history")
    conn.commit()
    conn.close()


# ============================================================
# 記憶（Memory Skill）
# ============================================================

def init_memory_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            content TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    conn.commit()
    conn.close()


def save_memory(user_id, content, category="general"):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO memories (user_id, content, category) VALUES (?, ?, ?)",
        (user_id, content, category),
    )
    mid = c.lastrowid
    conn.commit()
    conn.close()
    return f"🧠 已記住 #{mid}：{content}"


def get_user_memories(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "SELECT id, content, category FROM memories WHERE user_id=? ORDER BY updated_at DESC",
        (user_id,),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def update_memory(memory_id, content):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "UPDATE memories SET content=?, updated_at=datetime('now','localtime') WHERE id=?",
        (content, memory_id),
    )
    if c.rowcount == 0:
        conn.close()
        return f"❌ 找不到記憶 #{memory_id}"
    conn.commit()
    conn.close()
    return f"🧠 已更新記憶 #{memory_id}：{content}"


def delete_memory(memory_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM memories WHERE id=?", (memory_id,))
    if c.rowcount == 0:
        conn.close()
        return f"❌ 找不到記憶 #{memory_id}"
    conn.commit()
    conn.close()
    return f"🗑️ 已刪除記憶 #{memory_id}"


def list_user_memories(user_id):
    rows = get_user_memories(user_id)
    if not rows:
        return "🧠 目前沒有記住任何關於你的事。"
    lines = ["🧠 *關於你的記憶*\n"]
    for mid, content, category in rows:
        lines.append(f"• `#{mid}` [{category}] {content}")
    return "\n".join(lines)


def get_stats():
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
    return {"pending": pending, "done": done, "reminders": reminders, "chats": chats}