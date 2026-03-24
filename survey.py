"""調查/收集系統 - 私訊多人收集資訊並彙整"""

import sqlite3
from datetime import datetime, timedelta
from config import DB_PATH, MY_SLACK_USER_ID, logger


def get_db():
    return sqlite3.connect(DB_PATH)


def init_survey_db():
    """初始化調查相關的資料表"""
    conn = get_db()
    c = conn.cursor()

    # 調查任務
    c.execute("""
        CREATE TABLE IF NOT EXISTS surveys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            question TEXT NOT NULL,
            created_by TEXT NOT NULL,
            deadline TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # 調查對象與回覆
    c.execute("""
        CREATE TABLE IF NOT EXISTS survey_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            survey_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            user_name TEXT,
            response TEXT,
            responded_at TEXT,
            FOREIGN KEY (survey_id) REFERENCES surveys(id)
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# 建立調查
# ============================================================

def create_survey(title, question, user_ids, user_names, deadline_minutes=60, created_by=None):
    """建立一個新的調查任務

    Args:
        title: 調查標題（例如「明天下午會議時間」）
        question: 要問的問題
        user_ids: 要問的人的 Slack User ID 列表
        user_names: 對應的名稱列表
        deadline_minutes: 截止時間（分鐘後）
        created_by: 發起人的 User ID

    Returns:
        survey_id, deadline_str
    """
    deadline = (datetime.now() + timedelta(minutes=deadline_minutes)).strftime("%Y-%m-%d %H:%M")

    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO surveys (title, question, created_by, deadline) VALUES (?, ?, ?, ?)",
        (title, question, created_by or MY_SLACK_USER_ID, deadline),
    )
    survey_id = c.lastrowid

    # 為每個人建立一筆待回覆記錄
    for uid, name in zip(user_ids, user_names):
        c.execute(
            "INSERT INTO survey_responses (survey_id, user_id, user_name) VALUES (?, ?, ?)",
            (survey_id, uid, name),
        )

    conn.commit()
    conn.close()
    return survey_id, deadline


# ============================================================
# 記錄回覆
# ============================================================

def record_response(user_id, response_text):
    """記錄某人的回覆（找到他最近的未回覆調查）

    Returns:
        (survey_id, title) 如果成功，(None, None) 如果找不到
    """
    conn = get_db()
    c = conn.cursor()

    # 找這個人最近的未回覆調查
    c.execute("""
        SELECT sr.id, sr.survey_id, s.title
        FROM survey_responses sr
        JOIN surveys s ON sr.survey_id = s.id
        WHERE sr.user_id = ? AND sr.response IS NULL AND s.status = 'active'
        ORDER BY s.created_at DESC
        LIMIT 1
    """, (user_id,))

    row = c.fetchone()
    if not row:
        conn.close()
        return None, None

    response_id, survey_id, title = row
    c.execute(
        "UPDATE survey_responses SET response = ?, responded_at = datetime('now', 'localtime') WHERE id = ?",
        (response_text, response_id),
    )
    conn.commit()
    conn.close()
    return survey_id, title


# ============================================================
# 檢查與彙整
# ============================================================

def get_survey_status(survey_id):
    """取得調查狀態"""
    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT title, question, deadline, status FROM surveys WHERE id = ?", (survey_id,))
    survey = c.fetchone()
    if not survey:
        conn.close()
        return None

    title, question, deadline, status = survey

    c.execute(
        "SELECT user_name, response FROM survey_responses WHERE survey_id = ?",
        (survey_id,),
    )
    responses = c.fetchall()
    conn.close()

    responded = [(name, resp) for name, resp in responses if resp]
    pending = [name for name, resp in responses if not resp]

    return {
        "title": title,
        "question": question,
        "deadline": deadline,
        "status": status,
        "responded": responded,
        "pending": pending,
        "total": len(responses),
    }


def format_survey_result(survey_id):
    """格式化調查結果"""
    info = get_survey_status(survey_id)
    if not info:
        return "找不到這個調查欸"

    lines = [f"📊 *調查結果：{info['title']}*\n"]
    lines.append(f"📝 問題：{info['question']}")
    lines.append(f"⏰ 截止：{info['deadline']}\n")

    if info["responded"]:
        lines.append("✅ *已回覆：*")
        for name, resp in info["responded"]:
            lines.append(f"  • {name}：{resp}")

    if info["pending"]:
        lines.append(f"\n⏳ *還沒回覆（{len(info['pending'])} 人）：*")
        for name in info["pending"]:
            lines.append(f"  • {name}")

    lines.append(f"\n📈 進度：{len(info['responded'])}/{info['total']}")

    return "\n".join(lines)


def check_expired_surveys():
    """檢查是否有到期的調查，回傳需要彙整的 survey_id 列表"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM surveys WHERE status = 'active' AND deadline <= ?", (now,))
    rows = c.fetchall()

    expired_ids = []
    for (sid,) in rows:
        c.execute("UPDATE surveys SET status = 'completed' WHERE id = ?", (sid,))
        expired_ids.append(sid)

    conn.commit()
    conn.close()
    return expired_ids


def check_all_responded(survey_id):
    """檢查是否所有人都回覆了"""
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*) FROM survey_responses WHERE survey_id = ? AND response IS NULL",
        (survey_id,),
    )
    pending = c.fetchone()[0]
    conn.close()
    return pending == 0


def complete_survey(survey_id):
    """手動完成調查"""
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE surveys SET status = 'completed' WHERE id = ?", (survey_id,))
    conn.commit()
    conn.close()


def list_active_surveys():
    """列出進行中的調查"""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, title, deadline FROM surveys WHERE status = 'active' ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()

    if not rows:
        return "目前沒有進行中的調查"

    lines = ["📊 *進行中的調查*\n"]
    for sid, title, deadline in rows:
        lines.append(f"• `#{sid}` {title} — 截止 {deadline}")
    return "\n".join(lines)

def get_survey_creator(survey_id):
    """取得調查的發起人 User ID"""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT created_by FROM surveys WHERE id = ?", (survey_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None