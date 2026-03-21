"""LLM 整合 - OpenAI API、System Prompt、Tool 定義"""

import json
from datetime import datetime
from openai import OpenAI
from config import OPENAI_API_KEY, OPENAI_MODEL, logger
from db import (
    add_todo, list_todos, complete_todo, delete_todo,
    add_reminder, list_reminders, add_todo_with_reminder,
    save_chat, get_recent_chat,
)

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
- 如果用戶講諧音梗或冷笑話，要表現出無言的感覺，例如：「...」「蛤」「你認真ㄇ」「好喔（無言）」「😐」「這個我不行」
- 但還是會幫忙做事，只是會順便吐槽一下

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