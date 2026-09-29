import json
import os
import time
import requests
from bs4 import BeautifulSoup
# ==========================================
# 設定エリア
# ==========================================
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
CHECK_INTERVAL_SECONDS = 1800  # 30分ごとに監視
TOP_N = 10

RANKING_URL = "https://news.yahoo.co.jp/ranking/comment/entertainment"
CACHE_FILE = "previous_top10.json"

# 前回取得したTOP10のURLリストを保持
previous_top10_urls = []


def send_discord_ranking(ranking_items):
    """DiscordにNEWマーク付きでランキングTOP10を送信"""
    if "api/webhooks" not in DISCORD_WEBHOOK_URL:
        print("エラー: Webhook URLが正しいAPI用URLになっていません。")
        return

    description_lines = []
    for item in ranking_items:
        # メダル・数字の絵文字装飾
        rank_emoji = (
            "🥇"
            if item["rank"] == 1
            else (
                "🥈"
                if item["rank"] == 2
                else "🥉" if item["rank"] == 3 else f"**{item['rank']}.**"
            )
        )

        # 前回のTOP10に入っていなかった場合は 🆕 マークを付与！
        new_tag = " 🆕" if item["is_new"] else ""

        description_lines.append(
            f"{rank_emoji}{new_tag} [{item['title']}]({item['url']})"
        )

    embed = {
        "title": "🔥 【エンタメ】Yahoo!コメントランキング TOP10",
        "description": "\n\n".join(description_lines),
        "color": 0xFF4500,
        "footer": {
            "text": "🆕＝前回チェック時にTOP10外だった新着ネタ | 視聴者はこう見た"
        },
    }

    payload = {"embeds": [embed]}

    try:
        res = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if res.status_code == 204:
            print(
                f"[{time.strftime('%H:%M:%S')}] DiscordへNEWマーク付きTOP10通知を送信しました。"
            )
        else:
            print(f"送信失敗: Status Code {res.status_code}")
    except Exception as e:
        print(f"送信エラー: {e}")


def fetch_ranking():
    # 前回URLをファイルから読み込む
    previous_top10_urls = []
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                previous_top10_urls = json.load(f)
        except Exception as e:
            print(f"キャッシュ読み込みエラー: {e}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
    }

    try:
        res = requests.get(RANKING_URL, headers=headers, timeout=10)
        if res.status_code != 200:
            return []

        soup = BeautifulSoup(res.text, "html.parser")
        articles = soup.find_all(
            "a", href=lambda href: href and "/articles/" in href
        )

        ranking_items = []
        current_urls = []
        rank = 1
        seen_urls = set()

        for a in articles:
            url = a.get("href").split("?")[0]
            title = a.text.strip()

            if not title or url in seen_urls:
                continue

            seen_urls.add(url)
            current_urls.append(url)

            # キャッシュが存在していれば新着判定
            is_new = False
            if previous_top10_urls:
                is_new = url not in previous_top10_urls

            ranking_items.append(
                {"rank": rank, "title": title, "url": url, "is_new": is_new}
            )

            rank += 1
            if rank > TOP_N:
                break

        # 今回のURLリストをファイルに保存
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(current_urls, f, ensure_ascii=False)
        except Exception as e:
            print(f"キャッシュ保存エラー: {e}")

        return ranking_items

    except Exception as e:
        print(f"取得エラー: {e}")
        return []


def main():
    print("🚀 Yahoo!ニュース TOP10チェックを実行します。")

    # 1回だけ取得して送信
    items = fetch_ranking()
    if items:
        send_discord_ranking(items)

    print("🏁 処理が完了しました。")

if __name__ == "__main__":
    main()
