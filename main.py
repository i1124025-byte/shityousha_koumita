import time
import requests
from bs4 import BeautifulSoup

# ==========================================
# 設定エリア
# ==========================================
DISCORD_WEBHOOK_URL = (
    "https://discord.com/api/webhooks/YOUR_CORRECT_WEBHOOK_URL1554289880899788822/fNNeFxHka04snf4kxn0bvCTiNVI-aBiwHYiEVBF3rP7PU6X-3lI_UsroMjpjuiHa0mgN"
)
TOP_N = 10

RANKING_URL = "https://news.yahoo.co.jp/ranking/comment/entertainment"

# 前回取得したTOP10のURLリストを保持
previous_top10_urls = []


def send_discord_new_items(new_items):
    """新しくTOP10入りした記事（NEW）だけをDiscordに通知"""
    if "api/webhooks" not in DISCORD_WEBHOOK_URL:
        print("エラー: Webhook URLが正しいAPI用URLになっていません。")
        return

    description_lines = []
    for item in new_items:
        # ランクに応じた絵文字
        rank_emoji = (
            "🥇"
            if item["rank"] == 1
            else (
                "🥈"
                if item["rank"] == 2
                else "🥉" if item["rank"] == 3 else f"**{item['rank']}位**"
            )
        )
        description_lines.append(
            f"🆕 {rank_emoji} [{item['title']}]({item['url']})"
        )

    embed = {
        "title": f"🔥 新着ランクイン（{len(new_items)}件）",
        "description": "\n\n".join(description_lines),
        "color": 0xFF4500,  # 朱色
        "footer": {
            "text": "Yahoo!コメントランキングTOP10新着 | 視聴者はこう見た"
        },
    }

    payload = {"embeds": [embed]}

    try:
        res = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if res.status_code == 204:
            print(
                f"[{time.strftime('%H:%M:%S')}] DiscordへNEWネタ（{len(new_items)}件）を通知しました。"
            )
        else:
            print(f"送信失敗: Status Code {res.status_code}")
    except Exception as e:
        print(f"送信エラー: {e}")


def fetch_and_check_ranking():
    """ランキングを取得し、NEWアイテムだけを抽出"""
    global previous_top10_urls

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
    }

    try:
        res = requests.get(RANKING_URL, headers=headers, timeout=10)
        if res.status_code != 200:
            return

        soup = BeautifulSoup(res.text, "html.parser")
        articles = soup.find_all(
            "a", href=lambda href: href and "/articles/" in href
        )

        current_top10 = []
        rank = 1
        seen_urls = set()

        for a in articles:
            url = a.get("href").split("?")[0]
            title = a.text.strip()

            if not title or url in seen_urls:
                continue

            seen_urls.add(url)
            current_top10.append(
                {"rank": rank, "title": title, "url": url}
            )

            rank += 1
            if rank > TOP_N:
                break

        current_urls = [item["url"] for item in current_top10]

        # 初回実行時は基準を作るだけ（大量通知を防ぐため）
        if not previous_top10_urls:
            previous_top10_urls = current_urls
            print(
                f"[{time.strftime('%H:%M:%S')}] 監視を開始しました（初回の基準TOP10を記憶）。"
            )
            return

        # 前回のTOP10に含まれていなかった「NEWアイテム」だけを抽出
        new_items = [
            item
            for item in current_top10
            if item["url"] not in previous_top10_urls
        ]

        # NEWアイテムがあればDiscordに通知
        if new_items:
            send_discord_new_items(new_items)
            previous_top10_urls = current_urls
        else:
            print(
                f"[{time.strftime('%H:%M:%S')}] 新しいTOP10ランクイン記事はありませんでした。"
            )

    except Exception as e:
        print(f"処理エラー: {e}")


def main():
    # GitHub Actions（1回使い切り実行）と ローカル（ループ実行）の両対応
    fetch_and_check_ranking()


if __name__ == "__main__":
    main()
