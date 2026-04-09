"""/Command 指令系統 - 所有指令定義"""

from datetime import datetime
from config import OPENAI_MODEL, logger
from db import get_stats, clear_chat_history, list_user_memories
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


@command("/memories", "🧠 查看 Bot 記住了什麼")
def cmd_memories(event, say, args, **kwargs):
    user_id = event.get("user", "")
    say(list_user_memories(user_id))


@command("/wishlist", "🛒 好物清單（附圖辨識新增，無圖看清單）", needs_image=True)
def cmd_wishlist(event, say, args, handle_image_files=None, **kwargs):
    from config import SLACK_BOT_TOKEN, IMAGE_MIME_TYPES
    from vision import download_slack_file
    from wishlist import wishlist_recognize_and_add, wishlist_list_items

    files = event.get("files", [])
    image_files = [f for f in files if f.get("mimetype", "") in IMAGE_MIME_TYPES]

    if not image_files:
        # 有文字就讓 LLM 判斷篩選條件，沒文字就顯示全部
        if args.strip():
            say(chat_with_llm(f"查看好物清單：{args}", user_id=event.get("user")))
        else:
            say(wishlist_list_items())
        return

    for f in image_files:
        file_url = f.get("url_private", "")
        if not file_url:
            continue
        try:
            image_bytes, mime_type = download_slack_file(file_url, SLACK_BOT_TOKEN)
            filename = f.get("name", "image.jpg")
            result = wishlist_recognize_and_add(image_bytes, filename)
            say(result)
        except Exception as e:
            logger.error(f"Wishlist 辨識失敗: {e}")
            say(f"⚠️ 處理失敗：{str(e)}")


@command("/help", "📖 顯示可用指令")
def cmd_help(event, say, args, **kwargs):
    lines = ["📖 *可用指令*\n"]
    for name, info in COMMANDS.items():
        lines.append(f"• `{name}` — {info['description']}")
    lines.append("\n💬 也可以直接打字跟我聊天，不需要指令！")
    say("\n".join(lines))


# ============================================================
# 調查/收集指令
# ============================================================

@command("/survey", "📊 發起調查（收集時間、意見等）")
def cmd_survey(event, say, args, **kwargs):
    """發起一個調查
    用法：/survey @人1 @人2 @人3 | 問題內容 | 截止分鐘數(選填,預設60)
    範例：/survey @Alice @Bob | 明天下午什麼時間可以開會？ | 120
    """
    if not args:
        say(
            "📊 *調查用法*\n\n"
            "`/survey @人1 @人2 | 問題 | 截止分鐘數`\n\n"
            "範例：\n"
            "• `/survey @Alice @Bob | 明天下午什麼時間可以開會？`\n"
            "• `/survey @Alice @Bob @Charlie | 週五聚餐要吃什麼？ | 120`\n"
            "• `/survey @Alice | 你覺得 A 方案還是 B 方案好？ | 30`"
        )
        return

    import re
    from survey import create_survey

    parts = [p.strip() for p in args.split("|")]
    if len(parts) < 2:
        say("⚠️ 格式：`/survey @人1 @人2 | 問題`\n用 `|` 隔開人和問題喔")
        return

    # 解析 @mentions
    mentions_raw = parts[0]
    question = parts[1]
    deadline_minutes = int(parts[2]) if len(parts) > 2 else 60

    user_ids = re.findall(r"<@([A-Z0-9]+)>", mentions_raw)
    if not user_ids:
        say("⚠️ 要 tag 人喔，例如：`/survey @Alice @Bob | 問題`")
        return

    # 取得每個人的名字
    slack_app = kwargs.get("slack_app")
    user_names = []
    for uid in user_ids:
        try:
            if slack_app:
                info = slack_app.client.users_info(user=uid)
                user_names.append(info["user"]["real_name"] or info["user"]["name"])
            else:
                user_names.append(uid)
        except Exception:
            user_names.append(uid)

    # 建立調查
    created_by = event.get("user", "")
    survey_id, deadline = create_survey(
        title=question[:50],
        question=question,
        user_ids=user_ids,
        user_names=user_names,
        deadline_minutes=deadline_minutes,
        created_by=created_by,
    )

    # 私訊每個人
    for uid, name in zip(user_ids, user_names):
        try:
            if slack_app:
                dm = slack_app.client.conversations_open(users=[uid])
                dm_channel = dm["channel"]["id"]
                slack_app.client.chat_postMessage(
                    channel=dm_channel,
                    text=f"嗨 {name}！有個問題想問你：\n\n📝 *{question}*\n\n直接回覆我就好，截止時間：{deadline} ⏰",
                )
        except Exception as e:
            logger.error(f"發送調查給 {name} 失敗: {e}")

    say(
        f"📊 調查 #{survey_id} 已發出！\n"
        f"📝 問題：{question}\n"
        f"👥 已私訊 {len(user_ids)} 人\n"
        f"⏰ 截止時間：{deadline}\n\n"
        f"用 `/survey-status {survey_id}` 看進度"
    )


@command("/survey-status", "📈 查看調查進度")
def cmd_survey_status(event, say, args, **kwargs):
    from survey import format_survey_result, list_active_surveys

    if not args:
        say(list_active_surveys())
        return

    try:
        survey_id = int(args.strip().replace("#", ""))
        say(format_survey_result(survey_id))
    except ValueError:
        say("⚠️ 請提供調查編號，例如：`/survey-status 1`")


@command("/survey-close", "🔒 手動關閉調查並彙整")
def cmd_survey_close(event, say, args, **kwargs):
    from survey import complete_survey, format_survey_result

    if not args:
        say("⚠️ 請提供調查編號，例如：`/survey-close 1`")
        return

    try:
        survey_id = int(args.strip().replace("#", ""))
        complete_survey(survey_id)
        result = format_survey_result(survey_id)
        say(f"🔒 調查已關閉！\n\n{result}")
    except ValueError:
        say("⚠️ 請提供調查編號")