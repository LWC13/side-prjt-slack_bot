"""好物清單 API 串接 - 連接 wishlist-app"""

import requests
from config import logger

# wishlist-app 跑在同一台 EC2 上
WISHLIST_API_URL = "http://localhost:3000/api"

# 國家名稱正規化（對齊 wishlist-app 的 normalizeCountry）
COUNTRY_ALIASES = {
    "日本": "Japan", "jp": "Japan", "japan": "Japan",
    "韓國": "South Korea", "南韓": "South Korea", "kr": "South Korea", "korea": "South Korea", "south korea": "South Korea",
    "台灣": "Taiwan", "臺灣": "Taiwan", "tw": "Taiwan", "taiwan": "Taiwan",
    "美國": "United States", "us": "United States", "usa": "United States",
    "泰國": "Thailand", "th": "Thailand", "thailand": "Thailand",
}


def _normalize_country(country):
    if not country:
        return ""
    return COUNTRY_ALIASES.get(country.lower().strip(), country)


def wishlist_add_item(name, category="", country="", source_url="", description=""):
    """新增商品到清單"""
    try:
        resp = requests.post(
            f"{WISHLIST_API_URL}/items",
            data={
                "name": name,
                "category": category,
                "country": _normalize_country(country),
                "source_url": source_url,
                "description": description,
            },
            timeout=10,
        )
        if resp.status_code in (200, 201):
            msg = f"🛒 已加到清單：{name}"
            if category:
                msg += f"\n📂 分類：{category}"
            if country:
                msg += f"\n🌍 國家：{country}"
            if source_url:
                msg += f"\n🔗 來源：{source_url}"
            return msg
        else:
            logger.error(f"Wishlist add failed: HTTP {resp.status_code} body={resp.text}")
            return f"⚠️ 新增失敗：{resp.status_code} {resp.text}"
    except requests.ConnectionError:
        return "⚠️ 連不到清單服務，確認 wishlist-app 有在跑"
    except Exception as e:
        logger.error(f"Wishlist add error: {e}", exc_info=True)
        return f"⚠️ 新增失敗：{str(e)}"


def wishlist_list_items(country="", category=""):
    """取得清單"""
    try:
        params = {}
        if country:
            params["country"] = _normalize_country(country)
        if category:
            params["category"] = category

        resp = requests.get(f"{WISHLIST_API_URL}/items", params=params, timeout=10)
        if resp.status_code != 200:
            logger.error(f"Wishlist list failed: HTTP {resp.status_code} body={resp.text}")
            return f"⚠️ 取得清單失敗：{resp.status_code}"

        items = resp.json()
        if not items:
            return "清單是空的欸，還沒加任何東西"

        lines = [f"🛒 *好物清單*（共 {len(items)} 項）\n"]
        for item in items:
            icon = "✅" if item.get("purchased") else "⬜"
            line = f"{icon} `#{item['id']}` {item['name']}"
            if item.get("category"):
                line += f" [{item['category']}]"
            if item.get("country"):
                line += f" 🌍{item['country']}"
            lines.append(line)
        return "\n".join(lines)
    except requests.ConnectionError:
        return "⚠️ 連不到清單服務，確認 wishlist-app 有在跑"
    except Exception as e:
        logger.error(f"Wishlist list error: {e}", exc_info=True)
        return f"⚠️ 取得清單失敗：{str(e)}"


def wishlist_delete_item(item_id):
    """刪除商品"""
    try:
        resp = requests.delete(f"{WISHLIST_API_URL}/items/{item_id}", timeout=10)
        if resp.status_code == 200:
            return f"🗑️ 已從清單刪除 #{item_id}"
        else:
            logger.error(f"Wishlist delete failed: HTTP {resp.status_code} body={resp.text}")
            return f"⚠️ 刪除失敗：{resp.status_code} {resp.text}"
    except Exception as e:
        logger.error(f"Wishlist delete error: {e}", exc_info=True)
        return f"⚠️ 刪除失敗：{str(e)}"


def wishlist_toggle_purchased(item_id):
    """切換購買狀態"""
    try:
        resp = requests.patch(f"{WISHLIST_API_URL}/items/{item_id}/purchased", timeout=10)
        if resp.status_code == 200:
            return f"✅ 已更新 #{item_id} 的購買狀態"
        else:
            logger.error(f"Wishlist toggle failed: HTTP {resp.status_code} body={resp.text}")
            return f"⚠️ 更新失敗：{resp.status_code} {resp.text}"
    except Exception as e:
        logger.error(f"Wishlist toggle error: {e}", exc_info=True)
        return f"⚠️ 更新失敗：{str(e)}"


def wishlist_recognize_and_add(image_bytes, filename="image.jpg"):
    """把圖片轉發給 wishlist-app 辨識並新增到清單"""
    try:
        # 1. 辨識圖片
        recognize_resp = requests.post(
            f"{WISHLIST_API_URL}/recognize",
            files={"image": (filename, image_bytes)},
            timeout=30,
        )
        if recognize_resp.status_code != 200:
            return f"⚠️ 圖片辨識失敗：{recognize_resp.status_code} {recognize_resp.text}"

        info = recognize_resp.json()
        name = info.get("name", "")
        if not name:
            return "⚠️ 辨識不出這是什麼商品，試試手動輸入吧"

        # 2. 用辨識結果建立清單項目（帶圖片）
        create_resp = requests.post(
            f"{WISHLIST_API_URL}/items",
            data={
                "name": name,
                "category": info.get("category", ""),
                "description": info.get("description", ""),
                "source_url": info.get("source_url", ""),
                "country": info.get("country", ""),
            },
            files={"image": (filename, image_bytes)},
            timeout=15,
        )
        if create_resp.status_code not in (200, 201):
            return f"⚠️ 辨識成功但新增失敗：{create_resp.status_code} {create_resp.text}"

        msg = f"🛒 已辨識並加到清單：*{name}*"
        if info.get("category"):
            msg += f"\n📂 分類：{info['category']}"
        if info.get("country"):
            msg += f"\n🌍 國家：{info['country']}"
        if info.get("source_url"):
            msg += f"\n🔗 來源：{info['source_url']}"
        if info.get("description"):
            msg += f"\n📝 {info['description']}"
        return msg

    except requests.ConnectionError:
        return "⚠️ 連不到清單服務，確認 wishlist-app 有在跑"
    except Exception as e:
        logger.error(f"Wishlist recognize error: {e}", exc_info=True)
        return f"⚠️ 辨識失敗：{str(e)}"


def wishlist_get_filters():
    """取得可用的國家與分類篩選"""
    try:
        resp = requests.get(f"{WISHLIST_API_URL}/filters", timeout=10)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        return None