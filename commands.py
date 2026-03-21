"""/Command 指令系統 - 所有指令定義"""

from datetime import datetime
from config import OPENAI_MODEL, logger
from db import get_stats, clear_chat_history
from llm import chat_with_llm

# ============================================================
# 指令註冊機制
# ============================================================

COMMANDS = {}


def command(name, description, needs_image=False):
    """裝飾器：註冊一個 /command"""
    def decorator(func):
        COMMANDS[name] = {"handler": func, "description": description, "needs_image": needs_image}
        return func
    return decorator


# ============================================================
# 指令定義（加新指令只要加一個 @command 函數）
# ============================================================

@command("/reimbursement", "🧾 報帳分析（需附圖片）", needs_image=True)
def cmd_reimbursement(event, say, args, handle_image_files=None):
    from vision import EXPENSE_EXTRACT_PROMPT
    if handle_image_files and handle_image_files(event, say, user_text=EXPENSE_EXTRACT_PROMPT):
        return
    say("⚠️ 請附上收據或發票的圖片，再使用 `/reimbursement`")


@command("/summarize", "📝 摘要文字內容")
def cmd_summarize(event, say, args, **kwargs):
    if not args:
        say("⚠️ 請提供要摘要的內容，例如：`/summarize 一段很長的文字...`")
        return
    say(chat_with_llm(f"請幫我摘要以下內容，用繁體中文回答：\n{args}"))


@command("/translate", "🌐 翻譯成英文")
def cmd_translate(event, say, args, **kwargs):
    if not args:
        say("⚠️ 請提供要翻譯的內容，例如：`/translate 今天天氣很好`")
        return
    say(chat_with_llm(f"請將以下內容翻譯成英文，只輸出翻譯結果：\n{args}"))


@command("/status", "📊 顯示 Bot 狀態")
def cmd_status(event, say, args, **kwargs):
    stats = get_stats()
    say(
        f"📊 *Bot 狀態*\n"
        f"• 待辦：{stats['pending']} 項（已完成 {stats['done']} 項）\n"
        f"• 待提醒：{stats['reminders']} 項\n"
        f"• 對話紀錄：{stats['chats']} 則\n"
        f"• 模型：`{OPENAI_MODEL}`\n"
        f"• 時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )


@command("/clear", "🗑️ 清除對話記憶")
def cmd_clear(event, say, args, **kwargs):
    clear_chat_history()
    say("🗑️ 已清除所有對話記憶，重新開始！")


@command("/help", "📖 顯示可用指令")
def cmd_help(event, say, args, **kwargs):
    lines = ["📖 *可用指令*\n"]
    for name, info in COMMANDS.items():
        lines.append(f"• `{name}` — {info['description']}")
    lines.append("\n💬 也可以直接打字跟我聊天，不需要指令！")
    say("\n".join(lines))