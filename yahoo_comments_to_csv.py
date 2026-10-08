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
クラス名ではなく <article> 要素と「返信」「共感した」などの文言で行っている。
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

# 1つの <article> からコメント情報を抜き出す関数（他のJSから共通利用）
JS_EXTRACT_FN = r"""
(art) => {
    const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
    const num = (s) => {
        const m = clean(s).replace(/,/g, "").match(/(\d+)/);
        return m ? parseInt(m[1], 10) : 0;
    };

    // ユーザー名: 見出し or ユーザーページへのリンク
    let user = "";
    const h = art.querySelector("h2, h3, a[href*='/users/']");
    if (h) user = clean(h.textContent);

    // 投稿日時: <time> があれば優先、なければ「○分前」「10/8(水) 12:34」などの文字列
    let postedAt = "";
    const t = art.querySelector("time");
    if (t) {
        postedAt = clean(t.getAttribute("datetime") || t.textContent);
    } else {
        const m = clean(art.textContent).match(
            /(\d+(秒|分|時間|日)前|\d{1,2}\/\d{1,2}\([^)]\)\s*\d{1,2}:\d{2}|\d{4}\/\d{1,2}\/\d{1,2}[^ ]*)/
        );
        if (m) postedAt = m[1];
    }

    // 本文: ネストした返信(<article>)を除いた中で最も長い <p>
    let text = "";
    for (const p of art.querySelectorAll("p")) {
        if (p.closest("article") !== art) continue;
        const s = (p.innerText || p.textContent || "").trim();
        if (s.length > text.length) text = s;
    }

    // リアクション数・返信件数（ボタン/リンクの文言から判定）
    let empathy = 0, naruhodo = 0, uun = 0, replyCount = 0;
    for (const b of art.querySelectorAll("button, a, [role='button']")) {
        if (b.closest("article") !== art) continue;
        const label = clean(b.textContent) + " " + clean(b.getAttribute("aria-label"));
        if (label.includes("共感")) empathy = num(label);
        else if (label.includes("なるほど")) naruhodo = num(label);
        else if (label.includes("うーん")) uun = num(label);
        else if (/返信/.test(label) && /\d/.test(label)) replyCount = num(label);
    }

    return { user, posted_at: postedAt, text, empathy, naruhodo, uun, reply_count: replyCount };
}
"""

# ページ読み込み直後に表示されているトップレベルのコメントに印をつけて返す
JS_MARK_TOP_COMMENTS = r"""
() => {
    const extract = %s;
    const arts = [...document.querySelectorAll("article")].filter(
        (a) => !a.parentElement.closest("article") && !a.dataset.yccId
    );
    let seq = 0;
    const out = [];
    for (const a of arts) {
        const info = extract(a);
        if (!info.text) continue;  // 本文のないarticle（広告など）は除外
        a.dataset.yccId = "c" + (seq++);
        a.dataset.yccSeen = "1";
        info.id = a.dataset.yccId;
        out.push(info);
    }
    return out;
}
""" % JS_EXTRACT_FN

# 指定コメントの返信ボタンを押す。押せたら true
JS_CLICK_REPLY_TOGGLE = r"""
(id) => {
    const art = document.querySelector(`article[data-ycc-id="${id}"]`);
    if (!art) return false;
    const btns = [...art.querySelectorAll("button, a, [role='button']")].filter(
        (b) => b.closest("article") === art
    );
    // 「返信 12」「返信12件」のような件数付きのものを優先（返信投稿ボタンを避ける）
    const btn =
        btns.find((b) => /返信/.test(b.textContent) && /\d/.test(b.textContent)) ||
        btns.find((b) => /返信を(表示|見る)/.test(b.textContent));
    if (!btn) return false;
    if (btn.getAttribute("aria-expanded") === "true") return true;
    btn.click();
    return true;
}
"""

# 指定コメント付近の「もっと見る」系ボタンを1つ押す。押せたら true
JS_CLICK_MORE_REPLIES = r"""
(id) => {
    const art = document.querySelector(`article[data-ycc-id="${id}"]`);
    if (!art) return false;
    const scope = art.closest("li") || art.parentElement;
    const btn = [...scope.querySelectorAll("button, a, [role='button']")].find((b) => {
        const s = (b.textContent || "").replace(/\s+/g, "");
        return /(もっと見る|さらに表示|返信をもっと|続きの返信|もっと読む)/.test(s)
            && !b.closest("nav");
    });
    if (!btn) return false;
    btn.click();
    return true;
}
"""

# まだ印のついていない <article> 数（返信の読み込み検知用）
JS_COUNT_UNSEEN = r"""
() => [...document.querySelectorAll("article")].filter((a) => !a.dataset.yccSeen).length
"""

# 新たに出現した <article>（＝直前に展開したコメントの返信）を回収して印をつける
JS_COLLECT_NEW_REPLIES = r"""
() => {
    const extract = %s;
    const out = [];
    for (const a of document.querySelectorAll("article")) {
        if (a.dataset.yccSeen) continue;
        a.dataset.yccSeen = "1";
        const info = extract(a);
        if (info.text) out.push(info);
    }
    return out;
}
""" % JS_EXTRACT_FN


# ==========================================
# 処理本体
# ==========================================
def normalize_article_url(url):
    """記事URLから https://news.yahoo.co.jp/articles/<id> を取り出す"""
    m = re.search(r"https?://news\.yahoo\.co\.jp/articles/[0-9a-zA-Z]+", url)
    if not m:
        raise ValueError(f"Yahoo!ニュースの記事URLではありません: {url}")
    return m.group(0)


def wait_for_new_articles(page, before_count):
    """返信が読み込まれて <article> が増えるのを待つ（増えなければタイムアウトで抜ける）"""
    try:
        page.wait_for_function(
            f"() => ({JS_COUNT_UNSEEN})() > {before_count}", timeout=CLICK_WAIT_MS
        )
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(300)


def fetch_replies(page, comment_id):
    """コメントの返信をすべて展開して取得する"""
    replies = []
    if not page.evaluate(JS_CLICK_REPLY_TOGGLE, comment_id):
        return replies
    wait_for_new_articles(page, 0)
    replies.extend(page.evaluate(JS_COLLECT_NEW_REPLIES))

    # 「もっと見る」が出なくなるまで押し続ける
    while page.evaluate(JS_CLICK_MORE_REPLIES, comment_id):
        wait_for_new_articles(page, 0)
        new = page.evaluate(JS_COLLECT_NEW_REPLIES)
        if not new:
            break
        replies.extend(new)
    return replies


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
            page.wait_for_selector("article", timeout=10000)
        except PlaywrightTimeoutError:
            print(f"[{sort_label}] コメントが見つからないため終了します。")
            break

        comments = page.evaluate(JS_MARK_TOP_COMMENTS)
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
