"""
Slack Agent Bot - 個人 AI 助理
主程式：Slack 事件路由 + 啟動
"""

import re
from threading import Thread

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from config import SLACK_BOT_TOKEN, SLACK_APP_TOKEN, IMAGE_MIME_TYPES, logger
from db import init_db
from llm import chat_with_llm, set_slack_app
from commands import COMMANDS
from scheduler import background_scheduler
from vision import process_slack_image
from survey import init_survey_db, record_response, check_all_responded, complete_survey, format_survey_result

# ============================================================
# Slack App
# ============================================================

app = App(token=SLACK_BOT_TOKEN)
set_slack_app(app)


# ============================================================
# 圖片處理
# ============================================================

def handle_image_files(event, say, user_text=""):
    """檢查訊息是否包含圖片，有的話用 Gemini 分析"""
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
# 訊息處理
# ============================================================

def process_message(event, say):
    """統一處理訊息的核心邏輯（@mention 和 DM 共用）"""
    text = event.get("text", "")

    # 只移除 Bot 自己的 @mention，保留其他人的（給 create_survey 用）
    bot_user_id = getattr(app, '_bot_user_id', None)
    if not bot_user_id:
        try:
            auth = app.client.auth_test()
            bot_user_id = auth["user_id"]
            app._bot_user_id = bot_user_id
        except Exception:
            bot_user_id = None

    if bot_user_id:
        text = text.replace(f"<@{bot_user_id}>", "").strip()
    else:
        # fallback: 移除第一個 mention（通常是 Bot）
        text = re.sub(r"<@[A-Z0-9]+>", "", text, count=1).strip()

    # 1. /command 指令
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd_name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        if cmd_name in COMMANDS:
            COMMANDS[cmd_name]["handler"](event, say, args, handle_image_files=handle_image_files, slack_app=app)
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

    user_id = event.get("user", "")
    text = event.get("text", "").strip()

    # 先檢查是不是調查的回覆
    if text and user_id:
        survey_id, title = record_response(user_id, text)
        if survey_id:
            say(f"收到你的回覆了，謝啦 👍")
            logger.info(f"調查 #{survey_id} 收到 {user_id} 的回覆")

            # 檢查是否所有人都回覆了
            if check_all_responded(survey_id):
                complete_survey(survey_id)
                result = format_survey_result(survey_id)
                # 通知發起人
                from config import MY_SLACK_USER_ID
                if MY_SLACK_USER_ID:
                    try:
                        app.client.chat_postMessage(
                            channel=MY_SLACK_USER_ID,
                            text=f"🎉 所有人都回覆了！\n\n{result}",
                        )
                    except Exception as e:
                        logger.error(f"通知發起人失敗: {e}")
            return

    process_message(event, say)


# ============================================================
# 啟動
# ============================================================

def main():
    init_db()
    init_survey_db()
    logger.info("資料庫初始化完成")
    logger.info(f"已註冊 {len(COMMANDS)} 個指令：{', '.join(COMMANDS.keys())}")
    Thread(target=background_scheduler, args=(app,), daemon=True).start()
    logger.info("Slack Bot 啟動中...")
    SocketModeHandler(app, SLACK_APP_TOKEN).start()


if __name__ == "__main__":
    main()