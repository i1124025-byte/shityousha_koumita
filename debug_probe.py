"""一時的な調査用: 実際のコメントページの構造をログに出力する"""
import sys
from playwright.sync_api import sync_playwright

url = sys.argv[1].split("?")[0].rstrip("/") + "/comments?page=1&order=recommended"
JS = r"""
() => {
  const out = [];
  const desc = (e) => e.tagName.toLowerCase() + (e.id ? "#" + e.id : "") +
      (e.className && typeof e.className === "string" ? "." + e.className.trim().split(/\s+/).join(".") : "") +
      Object.entries(e.dataset || {}).map(([k,v]) => `[data-${k}=${v}]`).join("");
  out.push("TITLE " + document.title);
  out.push("COUNTS article=" + document.querySelectorAll("article").length +
           " li=" + document.querySelectorAll("li").length +
           " p=" + document.querySelectorAll("p").length);
  for (const s of document.querySelectorAll("script")) {
    const t = s.textContent || "";
    if (t.length > 500) out.push("SCRIPT id=" + s.id + " type=" + s.type + " len=" + t.length + " head=" + t.slice(0, 150).replace(/\s+/g, " "));
  }
  // 「共感」を含む最小要素を探す
  const hits = [...document.querySelectorAll("button, a, span, div")].filter(
    (e) => /共感/.test(e.textContent) && ![...e.children].some((c) => /共感/.test(c.textContent))
  );
  out.push("EMPATHY_HITS " + hits.length);
  for (const h of hits.slice(0, 3)) {
    let chain = [], e = h;
    for (let i = 0; i < 12 && e; i++, e = e.parentElement) chain.push(desc(e));
    out.push("CHAIN " + chain.join(" < "));
  }
  if (hits[0]) {
    let e = hits[0];
    for (let i = 0; i < 12 && e.parentElement; i++) {
      e = e.parentElement;
      if ((e.textContent || "").length > 150) break;
    }
    out.push("HTML1 " + e.outerHTML.slice(0, 6000));
  }
  if (hits[1]) {
    let e = hits[1];
    for (let i = 0; i < 12 && e.parentElement; i++) {
      e = e.parentElement;
      if ((e.textContent || "").length > 150) break;
    }
    out.push("HTML2 " + e.outerHTML.slice(0, 4000));
  }
  for (const a of [...document.querySelectorAll("article")].slice(0, 3))
    out.push("ARTICLE " + desc(a) + " :: " + a.outerHTML.slice(0, 1500));
  return out.join("\n");
}
"""
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(locale="ja-JP", user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
    pg.goto(url, wait_until="networkidle", timeout=60000)
    for _ in range(5):
        pg.mouse.wheel(0, 3000)
        pg.wait_for_timeout(800)
    print("URL", pg.url)
    print(pg.evaluate(JS))
    b.close()
