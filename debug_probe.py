"""一時的な調査用: 返信展開後の構造と通信をログに出力する"""
import re
import sys
from playwright.sync_api import sync_playwright

url = sys.argv[1].split("?")[0].rstrip("/") + "/comments?page=1&order=recommended"
reqs = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(locale="ja-JP", user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
    pg.goto(url, wait_until="networkidle", timeout=60000)
    st = pg.evaluate("() => { const s = window.__PRELOADED_STATE__ || {}; const walk=(o,d,pre)=>{ if(d>3||!o||typeof o!=='object') return []; return Object.keys(o).slice(0,40).flatMap(k=>{const v=o[k]; const t=Array.isArray(v)?'arr'+v.length:typeof v; return [pre+k+':'+t, ...walk(Array.isArray(v)?v[0]:v,d+1,pre+k+'.')];}); }; return walk(s,0,'').join('\\n'); }")
    print("STATE_KEYS\n" + st)
    pg.on("request", lambda r: reqs.append(r.method + " " + r.resource_type + " " + r.url) if r.resource_type in ("xhr", "fetch") else None)
    btn = pg.locator("button[data-cl-params*='_cl_link:opnre']").first
    btn.click()
    pg.wait_for_timeout(4000)
    print("REQS\n" + "\n".join(reqs[:20]))
    html = pg.evaluate("""() => { const b=document.querySelector("button[data-cl-params*='_cl_link:opnre']"); return b.closest('li').outerHTML; }""")
    html = re.sub(r"<svg.*?</svg>", "", html, flags=re.S)
    html = re.sub(r'<img [^>]*>', '', html)
    print("LIHTML len", len(html))
    for i in range(0, min(len(html), 24000), 3000):
        print("LI", html[i:i+3000])
    # 返信内の「もっと見る」系ボタンの文言一覧
    print("BTNS", pg.evaluate("""() => { const li=document.querySelector("button[data-cl-params*='_cl_link:opnre']").closest('li'); return [...li.querySelectorAll('button,a')].map(b => (b.getAttribute('data-cl-params')||'') + ' | ' + b.textContent.trim().slice(0,30)).join('\\n'); }"""))
    b.close()
