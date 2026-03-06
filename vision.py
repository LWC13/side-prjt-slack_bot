"""
圖片辨識模組 - 使用 Google GenAI SDK 分析圖片
"""

import os

import requests
from google import genai
from google.genai import types
import sys
from pathlib import Path

DEFAULT_MODEL = "gemini-2.0-flash"
DEFAULT_PROMPT = "請描述這張圖片的內容，用繁體中文回答。"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

EXPENSE_EXTRACT_PROMPT = """
請分析這張報支單圖片，擷取以下欄位的值。

只輸出 JSON，不要加任何說明文字或 markdown 符號。
如果欄位在圖片中不存在或看不清楚，該欄位輸出 null。

輸出格式：
{
  "申請人": "",
  "申請日期": "",
  "部門": "",
  "費用類別": "",
  "明細": [
    {
      "項目": "",
      "日期": "",
      "金額": "",
      "說明": ""
    }
  ],
  "小計": "",
  "稅額": "",
  "總金額": "",
  "審核人": "",
  "備註": ""
}
"""

def analyze_image(image_bytes: bytes, api_key: str = None, mime_type: str = "image/png",
                  prompt: str = None, model: str = DEFAULT_MODEL) -> str:
    """分析圖片。

    Args:
        image_bytes: 圖片的原始 bytes
        api_key: Gemini API key
        mime_type: 圖片 MIME type
        prompt: 使用者的問題（可選）
        model: Gemini 模型名稱

    Returns:
        Gemini 的回覆文字

    Raises:
        RuntimeError: API 呼叫失敗或回應異常時
    """
    key = api_key or GEMINI_API_KEY
    if not key:
        raise RuntimeError("未設定 GEMINI_API_KEY，請在 .env 中設定或傳入 api_key 參數")
    client = genai.Client(api_key=key)

    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

    response = client.models.generate_content(
        model=model,
        contents=[prompt or DEFAULT_PROMPT, image_part],
    )

    if not response.candidates:
        raise RuntimeError(
            f"Gemini 未回傳任何結果，可能被安全過濾器阻擋。"
            f"block_reason: {getattr(response.prompt_feedback, 'block_reason', 'unknown')}"
        )

    return response.text, response.usage_metadata


def download_slack_file(file_url: str, token: str) -> tuple[bytes, str]:
    """從 Slack 下載檔案。

    Args:
        file_url: Slack 檔案的 url_private
        token: Slack Bot Token

    Returns:
        (檔案內容 bytes, MIME type)

    Raises:
        RuntimeError: 下載失敗時
    """
    resp = requests.get(file_url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"下載 Slack 檔案失敗: HTTP {resp.status_code}")
    mime_type = resp.headers.get("Content-Type", "image/png").split(";")[0]
    return resp.content, mime_type


def process_slack_image(file_url: str, slack_token: str, api_key: str = None, prompt: str = None) -> str:
    """完整流程：從 Slack 下載圖片並用 Gemini 分析。

    Args:
        file_url: Slack 檔案的 url_private
        slack_token: Slack Bot Token
        api_key: Gemini API key
        prompt: 使用者的問題（可選）

    Returns:
        分析結果文字
    """
    image_bytes, mime_type = download_slack_file(file_url, slack_token)
    return analyze_image(image_bytes, api_key, mime_type, prompt)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

    # 從命令列讀取圖片路徑，預設用測試圖片
    image_path = sys.argv[1] if len(sys.argv) > 1 else "data/test_highspeed.jpeg"
    prompt = sys.argv[2] if len(sys.argv) > 2 else EXPENSE_EXTRACT_PROMPT

    path = Path(image_path)
    if not path.exists():
        print(f"❌ 找不到圖片：{image_path}")
        sys.exit(1)

    mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    mime_type = mime_map.get(path.suffix.lower(), "image/png")

    # print(f"📷 分析圖片：{image_path}（{mime_type}）")
    # print(f"💬 Prompt：{prompt or DEFAULT_PROMPT}\n")

    import time
    image_bytes = path.read_bytes()
    start = time.time()
    text, usage = analyze_image(image_bytes, mime_type=mime_type, prompt=prompt)
    elapsed = time.time() - start

    print("=== 分析結果 ===")
    print(text)
    print("\n=== Token 使用量 ===")
    print(usage)
    print(f"Input:  {usage.prompt_token_count}")
    print(f"Output: {usage.candidates_token_count}")
    print(f"Total:  {usage.total_token_count}")
    print(f"\n=== Inference Time ===")
    print(f"{elapsed:.2f}s")