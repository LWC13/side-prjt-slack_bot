"""背景排程 - 提醒、每日摘要、下班前檢查"""

import os
import time
from datetime import datetime
from config import MY_SLACK_USER_ID, DAILY_SUMMARY_HOUR, REMINDER_IMAGES, logger
from db import check_reminders, get_db


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
        dm = slack_app.client.conversations_open(users=[MY_SLACK_USER_ID])
        dm_channel_id = dm["channel"]["id"]
        slack_app.client.files_upload_v2(
            channel=dm_channel_id,
            file=image_path,
            initial_comment=f"⏰ *提醒*：{content}",
        )
    else:
        slack_app.client.chat_postMessage(
            channel=MY_SLACK_USER_ID,
            text=f"⏰ *提醒*：{content}",
        )


def generate_daily_summary():
    """產生每日摘要"""
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


def generate_eod_reminder():
    """產生下班前未完成待辦提醒"""
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

def is_workday():
    """判斷今天是不是工作日（週一到週五）"""
    return datetime.now().weekday() < 5  # 0=週一, 4=週五, 5=週六, 6=週日

def background_scheduler(slack_app):
    logger.info("背景排程已啟動")
    last_summary_date = None
    last_eod_check_date = None

    while True:
        try:
            now = datetime.now()
            # 檢查提醒（只有工作日才發重複提醒，一次性提醒照常發）
            for rid, content in check_reminders():
                if MY_SLACK_USER_ID:
                    try:
                        send_reminder(slack_app, content)
                        logger.info(f"已發送提醒 #{rid}")
                    except Exception as e:
                        logger.error(f"發送提醒失敗: {e}")

            # 每日摘要（只有工作日）
            if is_workday() and now.hour == DAILY_SUMMARY_HOUR and now.minute < 1 and last_summary_date != now.date() and MY_SLACK_USER_ID:
                try:
                    slack_app.client.chat_postMessage(channel=MY_SLACK_USER_ID, text=generate_daily_summary())
                    last_summary_date = now.date()
                    logger.info("已發送每日摘要")
                except Exception as e:
                    logger.error(f"發送每日摘要失敗: {e}")

            # 下班前待辦提醒（只有工作日）
            if is_workday() and now.hour == 17 and 30 <= now.minute < 31 and last_eod_check_date != now.date() and MY_SLACK_USER_ID:
                try:
                    eod_msg = generate_eod_reminder()
                    if eod_msg:
                        slack_app.client.chat_postMessage(channel=MY_SLACK_USER_ID, text=eod_msg)
                        logger.info("已發送下班前待辦提醒")
                    last_eod_check_date = now.date()
                except Exception as e:
                    logger.error(f"發送下班前提醒失敗: {e}")

            # 檢查到期的調查（這個不分假日，調查隨時都可能到期）
            try:
                from survey import check_expired_surveys, format_survey_result,get_survey_creator
                for sid in check_expired_surveys():
                    if MY_SLACK_USER_ID:
                        result = format_survey_result(sid)
                        creator = get_survey_creator(sid)
                        if creator:
                            slack_app.client.chat_postMessage(
                                channel=creator,
                                text=f"⏰ 調查截止了！\n\n{result}",
                            )
                        logger.info(f"調查 #{sid} 已截止，已發送彙整")
            except Exception as e:
                logger.error(f"檢查調查失敗: {e}")

        except Exception as e:
            logger.error(f"排程錯誤: {e}")

        time.sleep(30)