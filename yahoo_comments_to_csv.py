"""
Yahoo!ニュース記事のコメントをCSV出力するスクリプト

- おすすめ順: 上位100件 + 各コメントへの返信をすべて取得
- 新着順    : 上位100件（返信は取得しない）

使い方:
    pip install -r requirements-comments.txt
    playwright install chromium
    python yahoo_comments_to_csv.py https://news.yahoo.co.jp/articles/xxxxxxxx

    # 出力先を指定する場合
    python yahoo_comments_to_csv.py <記事URL> -o comments.csv

    # ブラウザを表示して動作確認したい場合
    python yahoo_comments_to_csv.py <記事URL> --headful

Yahoo!ニュースのクラス名は自動生成で頻繁に変わるため、要素の特定は
クラス名ではなくボタンの data-cl-params 属性（計測用パラメータ）で行っている。
ページ構造が変わって取得できなくなった場合は JS_* 定数を調整すること。
"""

import argparse
import csv
import re
import sys
import time

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

# ==========================================
# 設定エリア
# ==========================================
MAX_COMMENTS = 100
COMMENTS_PER_PAGE = 10
SORT_ORDERS = {
    "recommended": "おすすめ順",
    "newer": "新着順",
}
PAGE_WAIT_SECONDS = 1.0  # ページ遷移ごとの待機（アクセス負荷軽減）
CLICK_WAIT_MS = 4000  # 返信展開・もっと見るの読み込み待ち上限
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

CSV_COLUMNS = [
    "sort",  # おすすめ順 / 新着順
    "rank",  # 並び順での順位（返信は親コメントの順位）
    "type",  # comment / reply
    "reply_index",  # 返信の通し番号（コメントは空）
    "user",
    "posted_at",
    "text",
    "empathy",  # 共感した
    "naruhodo",  # なるほど
    "uun",  # うーん
    "reply_count",  # 表示上の返信件数（コメントのみ）
]

# ==========================================
# ページ内で実行するJavaScript
# ==========================================
# Yahoo!ニュースのコメント欄は、ボタンの data-cl-params 属性に
#   _cl_vmodule:cmt_usr（コメント）/ rep（返信）
#   _cl_link:agbtn1（共感した）/ agbtn2（なるほど）/ disbtn1（うーん）
#   _cl_link:opnre（返信を開く）/ cmtload + more（返信のもっと見る）
#   cmt_id:<コメントID>
# が入っているので、これを手がかりにコメントを特定する。
# （末尾に cl が付く agbtn1cl などは押下後表示用の非表示ボタンなので使わない）

# vmodule（cmt_usr / rep）のコメントを、scope 要素内から順番に抜き出す
JS_EXTRACT_COMMENTS = r"""
([vmodule, scopeId]) => {
    const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
    const num = (s) => {
        const m = clean(s).replace(/,/g, "").match(/(\d+)/);
        return m ? parseInt(m[1], 10) : 0;
    };
    const scope = scopeId
        ? document.querySelector(`[data-ycc-scope="${scopeId}"]`)
        : document;
    if (!scope) return [];
    const sel = (link) => `button[data-cl-params*="_cl_vmodule:${vmodule};_cl_link:${link};"]`;

    const out = [];
    const seen = new Set();
    for (const anchor of scope.querySelectorAll(sel("agbtn1"))) {
        const m = (anchor.getAttribute("data-cl-params") || "").match(/cmt_id:([^;]+)/);
        if (!m || seen.has(m[1])) continue;
        const id = m[1];
        seen.add(id);
        const art = anchor.closest("article");
        if (!art) continue;
        const own = (e) => e.closest("article") === art;
        const btn = (link) => [...art.querySelectorAll(sel(link))].find(own);

        const h = [...art.querySelectorAll("h2, a[data-cl-params*='_cl_link:profnm;']")].find(own);
        const t = [...art.querySelectorAll("time")].find(own);
        let text = "";
        for (const p of art.querySelectorAll("p")) {
            if (!own(p)) continue;
            const s = (p.innerText || p.textContent || "").trim();
            if (s.length > text.length) text = s;
        }
        const reply = [...art.querySelectorAll(sel("opnre"))].find(own);

        out.push({
            id,
            user: h ? clean(h.textContent) : "",
            posted_at: t ? clean(t.textContent) : "",
            text,
            empathy: btn("agbtn1") ? num(btn("agbtn1").textContent) : 0,
            naruhodo: btn("agbtn2") ? num(btn("agbtn2").textContent) : 0,
            uun: btn("disbtn1") ? num(btn("disbtn1").textContent) : 0,
            reply_count: reply ? num(reply.textContent) : 0,
        });
    }
    return out;
}
"""

# コメントを囲む <li> に目印をつけ、返信を開くボタンを押す。押せたら true
JS_OPEN_REPLIES = r"""
(cmtId) => {
    const btn = document.querySelector(
        `button[data-cl-params*="_cl_link:opnre;"][data-cl-params*="cmt_id:${cmtId};"]`
    );
    if (!btn) return false;
    const scope = btn.closest("li") || btn.closest("article").parentElement;
    scope.dataset.yccScope = cmtId;
    btn.click();
    return true;
}
"""

# 返信の「もっと見る」ボタンを押す。押せたら true
JS_CLICK_MORE_REPLIES = r"""
(cmtId) => {
    const scope = document.querySelector(`[data-ycc-scope="${cmtId}"]`);
    if (!scope) return false;
    const btn = scope.querySelector(
        'button[data-cl-params*="_cl_vmodule:cmtload;"][data-cl-params*="_cl_link:more;"]'
    );
    if (!btn) return false;
    btn.click();
    return true;
}
"""

# 目印をつけた範囲内に表示されている返信の数
JS_COUNT_REPLIES = r"""
(cmtId) => {
    const scope = document.querySelector(`[data-ycc-scope="${cmtId}"]`);
    if (!scope) return 0;
    const ids = new Set();
    for (const b of scope.querySelectorAll('button[data-cl-params*="_cl_vmodule:rep;_cl_link:agbtn1;"]')) {
        const m = (b.getAttribute("data-cl-params") || "").match(/cmt_id:([^;]+)/);
        if (m) ids.add(m[1]);
    }
    return ids.size;
}
"""


# ==========================================
# 処理本体
# ==========================================
def normalize_article_url(url):
    """記事URLから https://news.yahoo.co.jp/articles/<id> を取り出す"""
    m = re.search(r"https?://news\.yahoo\.co\.jp/articles/[0-9a-zA-Z]+", url)
    if not m:
        raise ValueError(f"Yahoo!ニュースの記事URLではありません: {url}")
    return m.group(0)


def wait_for_more_replies(page, comment_id, before_count):
    """返信が読み込まれて件数が増えるのを待つ（増えなければタイムアウトで抜ける）"""
    try:
        page.wait_for_function(
            f"(id) => ({JS_COUNT_REPLIES})(id) > {before_count}",
            arg=comment_id,
            timeout=CLICK_WAIT_MS,
        )
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(300)
    return page.evaluate(JS_COUNT_REPLIES, comment_id)


def fetch_replies(page, comment_id):
    """コメントの返信をすべて展開して取得する"""
    if not page.evaluate(JS_OPEN_REPLIES, comment_id):
        return []
    count = wait_for_more_replies(page, comment_id, 0)

    # 「もっと見る」が出なくなる（または件数が増えなくなる）まで押し続ける
    while page.evaluate(JS_CLICK_MORE_REPLIES, comment_id):
        new_count = wait_for_more_replies(page, comment_id, count)
        if new_count <= count:
            break
        count = new_count

    return page.evaluate(JS_EXTRACT_COMMENTS, ["rep", comment_id])


def scrape_sort(page, article_url, order, with_replies):
    sort_label = SORT_ORDERS[order]
    rows = []
    rank = 0
    page_no = 1

    while rank < MAX_COMMENTS:
        url = f"{article_url}/comments?page={page_no}&order={order}"
        print(f"[{sort_label}] {page_no}ページ目を取得中: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_selector(
                'button[data-cl-params*="_cl_vmodule:cmt_usr;_cl_link:agbtn1;"]',
                timeout=15000,
            )
        except PlaywrightTimeoutError:
            print(f"[{sort_label}] コメントが見つからないため終了します。")
            break

        comments = page.evaluate(JS_EXTRACT_COMMENTS, ["cmt_usr", None])
        if not comments:
            break

        for c in comments:
            if rank >= MAX_COMMENTS:
                break
            rank += 1
            rows.append(
                {
                    "sort": sort_label,
                    "rank": rank,
                    "type": "comment",
                    "reply_index": "",
                    **{k: c[k] for k in CSV_COLUMNS if k in c},
                }
            )

            if with_replies and c.get("reply_count", 0) > 0:
                replies = fetch_replies(page, c["id"])
                print(
                    f"  {rank}件目: 返信 {len(replies)}/{c['reply_count']}件を取得"
                )
                for i, r in enumerate(replies, start=1):
                    rows.append(
                        {
                            "sort": sort_label,
                            "rank": rank,
                            "type": "reply",
                            "reply_index": i,
                            **{k: r[k] for k in CSV_COLUMNS if k in r},
                            "reply_count": "",
                        }
                    )

        if len(comments) < COMMENTS_PER_PAGE:
            break  # 最終ページ
        page_no += 1
        time.sleep(PAGE_WAIT_SECONDS)

    print(f"[{sort_label}] コメント {rank}件を取得しました。")
    return rows


def main():
    parser = argparse.ArgumentParser(
        description="Yahoo!ニュース記事のコメント（おすすめ順100件+返信、新着順100件）をCSV出力"
    )
    parser.add_argument("url", help="Yahoo!ニュースの記事URL")
    parser.add_argument("-o", "--output", help="出力CSVファイル名（省略時は記事IDから自動生成）")
    parser.add_argument("--headful", action="store_true", help="ブラウザを表示して実行")
    args = parser.parse_args()

    try:
        article_url = normalize_article_url(args.url)
    except ValueError as e:
        print(e)
        sys.exit(1)

    output = args.output or f"comments_{article_url.rsplit('/', 1)[-1]}.csv"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headful)
        context = browser.new_context(user_agent=USER_AGENT, locale="ja-JP")
        page = context.new_page()
        try:
            rows = scrape_sort(page, article_url, "recommended", with_replies=True)
            rows += scrape_sort(page, article_url, "newer", with_replies=False)
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
