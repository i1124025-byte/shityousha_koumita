"""一時的な調査用: コメントAPIの応答形式をログに出力する"""
import json
import sys
from playwright.sync_api import sync_playwright

art = sys.argv[1].split("?")[0].rstrip("/")
aid = art.rsplit("/", 1)[-1]
base = f"https://news.yahoo.co.jp/api/comment/properties/news_user/articles/{aid}/comments"
cid = "6687c26d-95fd-4edc-b6d8-23901ac54bb1"
tries = [
    f"{base}/{cid}/reply?start=1&results=10&sort=recommendation",
    f"{base}/{cid}/reply?start=11&results=10&sort=recommendation",
    f"{base}?start=1&results=10&sort=recommendation",
    f"{base}?start=1&results=10&sort=newer",
    f"{base}?start=1&results=10&sort=time",
    f"{base}?start=1&results=10&sort=new",
]
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(locale="ja-JP", user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
    pg.goto(art + "/comments?page=1&order=recommended", wait_until="networkidle", timeout=60000)
    for u in tries:
        r = pg.evaluate("async (u) => { const r = await fetch(u, {credentials: 'include'}); return [r.status, await r.text()]; }", u)
        print("TRY", u, "STATUS", r[0])
        print("BODY", r[1][:2500].replace("\n", " "))
    # ページ側の新着順URLの確認
    pg.goto(art + "/comments?page=1&order=newer", wait_until="networkidle", timeout=60000)
    print("NEWER_URL", pg.url)
    print("NEWER_TIMES", pg.evaluate("() => [...document.querySelectorAll(\"a[data-cl-params*='_cl_link:prmtime']\")].map(a => a.textContent).join(', ')"))
    print("SORT_LINKS", pg.evaluate("() => [...document.querySelectorAll('a')].filter(a => /順/.test(a.textContent)).map(a => a.textContent.trim() + ' -> ' + a.href).join(' | ')"))
    b.close()
