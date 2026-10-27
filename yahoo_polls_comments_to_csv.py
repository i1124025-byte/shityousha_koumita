"""
Yahoo!ニュース「みんなの意見」のコメントをCSV出力するスクリプト

- おすすめ順: 上位100件 + 各コメントへの返信をすべて取得

使い方:
    pip install -r requirements-comments.txt
    playwright install chromium   # Google Chrome が入っていれば不要
    python yahoo_polls_comments_to_csv.py https://news.yahoo.co.jp/polls/74560

    # 出力先を指定する場合
    python yahoo_polls_comments_to_csv.py <みんなの意見URL> -o comments.csv

みんなの意見のコメント欄はニュース記事と同じ作りなので、
コメント・返信の抽出処理は yahoo_comments_to_csv.py のものを使う。
"""

import argparse
import csv
import re
import sys

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from yahoo_comments_to_csv import CSV_COLUMNS, USER_AGENT, scrape_sort


def normalize_polls_url(url):
    """みんなの意見のURLから https://news.yahoo.co.jp/polls/<id> を取り出す"""
    m = re.search(r"https?://news\.yahoo\.co\.jp/polls/\d+", url)
    if not m:
        raise ValueError(f"Yahoo!ニュース「みんなの意見」のURLではありません: {url}")
    return m.group(0)


def launch_browser(p, headless):
    """インストール済みの Google Chrome を優先し、無ければ Playwright の Chromium を使う"""
    try:
        return p.chromium.launch(channel="chrome", headless=headless)
    except PlaywrightError:
        return p.chromium.launch(headless=headless)


def main():
    parser = argparse.ArgumentParser(
        description="Yahoo!ニュース「みんなの意見」のコメント（おすすめ順100件+返信）をCSV出力"
    )
    parser.add_argument("url", help="みんなの意見のURL（例: https://news.yahoo.co.jp/polls/74560）")
    parser.add_argument("-o", "--output", help="出力CSVファイル名（省略時は polls_comments_<ID>.csv）")
    parser.add_argument("--headful", action="store_true", help="ブラウザを表示して実行")
    args = parser.parse_args()

    try:
        polls_url = normalize_polls_url(args.url)
    except ValueError as e:
        print(e)
        sys.exit(1)

    output = args.output or f"polls_comments_{polls_url.rsplit('/', 1)[-1]}.csv"

    with sync_playwright() as p:
        browser = launch_browser(p, headless=not args.headful)
        context = browser.new_context(user_agent=USER_AGENT, locale="ja-JP")
        page = context.new_page()
        try:
            rows = scrape_sort(page, polls_url, "recommended", with_replies=True)
        finally:
            browser.close()

    # Excelで文字化けしないよう BOM付きUTF-8 で出力
    with open(output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"🏁 {len(rows)}行を {output} に出力しました。")


if __name__ == "__main__":
    main()
