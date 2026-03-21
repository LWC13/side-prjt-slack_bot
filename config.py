"""設定檔 - 所有環境變數和常數"""

import os
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("slack-agent-bot")

# Slack
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN")

# OpenAI
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")

# Notion（之後串接用）
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")

# 個人設定
MY_SLACK_USER_ID = os.environ.get("MY_SLACK_USER_ID", "")
DAILY_SUMMARY_HOUR = int(os.environ.get("DAILY_SUMMARY_HOUR", "9"))

# 資料庫
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "agent.db"))

# 圖片
IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}

# 提醒附圖對應
REMINDER_IMAGES = {
    "喝水": "images/drink_water.jpg",
    # "運動": "images/exercise.png",
    # "吃藥": "images/medicine.png",
}