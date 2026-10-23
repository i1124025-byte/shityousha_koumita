<?php
/**
 * Yahoo!ニュース ヤフコメランキングを CSV に出力するスクリプト
 *   - 総合: TOP10
 *   - 各カテゴリー（国内・国際・経済・エンタメ・スポーツ・IT・科学・ライフ・地域）: TOP5
 *
 *   - ランキングに載った各記事のコメント: おすすめ順 上位30件（返信は含まない）
 *
 * 出力ファイル（2つ）:
 *   ランキング: collected_at,category,rank,title,url,comments_per_hour,published_at
 *   コメント  : collected_at,article_url,title,comment_rank,user,text,empathy,naruhodo,uun
 *               （総合とカテゴリーで同じ記事が重なっても、コメントは1回だけ取得する）
 *
 * 使い方:
 *   php yahoo_comment_ranking.php
 *     → data/yahoo_comment_ranking_YYYYMMDD.csv と data/yahoo_comment_ranking_YYYYMMDD_comments.csv に出力
 *   php yahoo_comment_ranking.php /path/to/out.csv
 *     → /path/to/out.csv と /path/to/out_comments.csv に出力
 *
 * Cron（ConoHa WING「サーバー管理 > ジョブスケジューラー」）例: 毎朝7時
 *   /usr/bin/php /home/<ユーザー名>/yahoo_ranking/yahoo_comment_ranking.php
 *   （ログを残す場合）... yahoo_comment_ranking.php >> /home/<ユーザー名>/yahoo_ranking/cron.log 2>&1
 *
 * Yahoo!ニュースのクラス名は自動生成で頻繁に変わるため、要素の特定はクラス名ではなく
 * data-cl-params 属性（_cl_vmodule:ranking / _cl_position:N）とタグ構造で行っている。
 */

date_default_timezone_set('Asia/Tokyo');

// ==========================================
// 設定エリア
// ==========================================
const RANKING_URL = 'https://news.yahoo.co.jp/ranking/comment';
// 取得するランキング: [カテゴリー名, URLの末尾, 取得件数]
const CATEGORIES = [
    ['総合', '', 10],
    ['国内', 'domestic', 5],
    ['国際', 'world', 5],
    ['経済', 'business', 5],
    ['エンタメ', 'entertainment', 5],
    ['スポーツ', 'sports', 5],
    ['IT・科学', 'it-science', 5],
    ['ライフ', 'life', 5],
    ['地域', 'local', 5],
];
const PAGE_WAIT_SECONDS = 1;    // ページ取得間の待機（アクセス負荷軽減）
const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    . '(KHTML, like Gecko) Chrome/124.0 Safari/537.36';
const OUTPUT_DIR = __DIR__ . '/data';

// 各記事から取得するコメント数（おすすめ順、1ページ10件）
const COMMENTS_PER_ARTICLE = 30;
const COMMENTS_PER_PAGE = 10;

const CSV_COLUMNS = ['collected_at', 'category', 'rank', 'title', 'url', 'comments_per_hour', 'published_at'];
const COMMENT_CSV_COLUMNS = [
    'collected_at', 'article_url', 'title', 'comment_rank', 'user', 'text', 'empathy', 'naruhodo', 'uun',
];

// ==========================================
// 処理本体
// ==========================================
function logMessage(string $message): void
{
    fwrite(STDERR, '[' . date('Y-m-d H:i:s') . "] {$message}\n");
}

function fetchHtml(string $url): string
{
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_FOLLOWLOCATION => true,
        CURLOPT_TIMEOUT => 30,
        CURLOPT_USERAGENT => USER_AGENT,
        CURLOPT_HTTPHEADER => ['Accept-Language: ja-JP,ja;q=0.9'],
        CURLOPT_ENCODING => '',
    ]);
    $html = curl_exec($ch);
    $status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    $error = curl_error($ch);
    curl_close($ch);

    if ($html === false || $status !== 200) {
        throw new RuntimeException("取得失敗: {$url} (HTTP {$status}) {$error}");
    }
    return $html;
}

/**
 * 「10/16(金) 17:04」を「2026-10-16 17:04」に変換する。
 * 年は表示されないので取得日時から補完する（年末年始のずれは前年扱い）。
 */
function normalizePublishedAt(string $text, DateTimeImmutable $now): string
{
    if (!preg_match('#(\d{1,2})/(\d{1,2})\D*?(\d{1,2}):(\d{2})#u', $text, $m)) {
        return $text;
    }
    $year = (int)$now->format('Y');
    if ((int)$m[1] > (int)$now->format('n')) {
        $year--;
    }
    return sprintf('%04d-%02d-%02d %02d:%02d', $year, $m[1], $m[2], $m[3], $m[4]);
}

/**
 * ランキングページの HTML から記事一覧を取り出す
 * @return array<int, array{rank:int,title:string,url:string,comments_per_hour:string,published_text:string}>
 */
function parseRanking(string $html): array
{
    $dom = new DOMDocument();
    libxml_use_internal_errors(true);
    $dom->loadHTML('<?xml encoding="UTF-8">' . $html);
    libxml_clear_errors();
    $xpath = new DOMXPath($dom);

    $items = [];
    $links = $xpath->query(
        '//ol[contains(concat(" ", normalize-space(@class), " "), " newsFeed_list ")]'
        . '/li/a[contains(@data-cl-params, "_cl_vmodule:ranking;")]'
    );
    foreach ($links as $a) {
        /** @var DOMElement $a */
        if (!preg_match('/_cl_position:(\d+);/', $a->getAttribute('data-cl-params'), $m)) {
            continue;
        }
        $rank = (int)$m[1];

        // タイトルは newsFeed_item_body 内の「子要素を持たない最初の div」
        $titleNode = $xpath->query(
            './/div[contains(@class, "newsFeed_item_body")]//div[not(*) and normalize-space()]',
            $a
        )->item(0);
        $timeNode = $xpath->query('.//time', $a)->item(0);
        // 「<em>627</em>件/時」
        $countNode = $xpath->query('.//p[contains(., "件/時")]/em', $a)->item(0);

        $items[] = [
            'rank' => $rank,
            'title' => $titleNode ? trim(preg_replace('/\s+/u', ' ', $titleNode->textContent)) : '',
            'url' => preg_replace('/\?.*$/', '', $a->getAttribute('href')),
            'comments_per_hour' => $countNode ? str_replace(',', '', trim($countNode->textContent)) : '',
            'published_text' => $timeNode ? trim($timeNode->textContent) : '',
        ];
    }
    return $items;
}

function collectRanking(string $url, int $limit): array
{
    logMessage("取得中: {$url}");
    $items = array_filter(parseRanking(fetchHtml($url)), fn($item) => $item['rank'] <= $limit);
    usort($items, fn($a, $b) => $a['rank'] <=> $b['rank']);
    return $items;
}

/**
 * 記事のコメントをおすすめ順に取得する（返信は含まない）。
 * コメントページの __PRELOADED_STATE__（埋め込みJSON）の commentFull.userCommentList を使う。
 * おすすめ順はページ読み込みごとに多少入れ替わるため、commentId で重複を除く。
 * @return array<int, array{user:string,text:string,empathy:int,naruhodo:int,uun:int}>
 */
function collectComments(string $articleUrl, int $limit): array
{
    $comments = [];
    $maxPage = (int)ceil($limit / COMMENTS_PER_PAGE);
    for ($page = 1; $page <= $maxPage && count($comments) < $limit; $page++) {
        if ($page > 1) {
            sleep(PAGE_WAIT_SECONDS);
        }
        $html = fetchHtml("{$articleUrl}/comments?page={$page}&order=recommended");
        if (!preg_match('#__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*</script>#s', $html, $m)) {
            throw new RuntimeException('コメントデータが見つかりません（ページ構造が変わった可能性があります）');
        }
        $state = json_decode($m[1], true);
        $list = $state['commentFull']['userCommentList'] ?? null;
        if (!is_array($list)) {
            throw new RuntimeException('コメント一覧が見つかりません（ページ構造が変わった可能性があります）');
        }
        foreach ($list as $c) {
            $id = $c['commentId'] ?? '';
            if ($id === '' || isset($comments[$id]) || count($comments) >= $limit) {
                continue;
            }
            $comments[$id] = [
                'user' => (string)($c['name'] ?? ''),
                'text' => (string)($c['text'] ?? ''),
                'empathy' => (int)($c['empathyCount'] ?? 0),
                'naruhodo' => (int)($c['insightCount'] ?? 0),
                'uun' => (int)($c['negativeCount'] ?? 0),
            ];
        }
        if (count($list) < COMMENTS_PER_PAGE) {
            break; // 最終ページ
        }
    }
    return array_values($comments);
}

function writeCsv(string $path, array $rows, array $columns = CSV_COLUMNS): void
{
    $dir = dirname($path);
    if (!is_dir($dir) && !mkdir($dir, 0755, true)) {
        throw new RuntimeException("出力先ディレクトリを作成できません: {$dir}");
    }
    $fp = fopen($path, 'w');
    if ($fp === false) {
        throw new RuntimeException("CSVを書き込めません: {$path}");
    }
    // Excelで文字化けしないよう BOM付きUTF-8 で出力
    fwrite($fp, "\xEF\xBB\xBF");
    fputcsv($fp, $columns, ',', '"', '');
    foreach ($rows as $row) {
        fputcsv($fp, array_map(fn($c) => $row[$c], $columns), ',', '"', '');
    }
    fclose($fp);
}

function main(array $argv): int
{
    $now = new DateTimeImmutable();
    $output = $argv[1] ?? OUTPUT_DIR . '/yahoo_comment_ranking_' . $now->format('Ymd') . '.csv';
    $commentOutput = preg_replace('/(\.csv)?$/i', '_comments.csv', $output, 1);

    $collectedAt = $now->format('Y-m-d H:i:s');
    $rows = [];
    $failed = [];

    // 1カテゴリーで失敗しても、他のカテゴリーは出力する
    foreach (CATEGORIES as $i => [$category, $slug, $limit]) {
        if ($i > 0) {
            sleep(PAGE_WAIT_SECONDS);
        }
        try {
            $items = collectRanking($slug === '' ? RANKING_URL : RANKING_URL . '/' . $slug, $limit);
            if (!$items) {
                throw new RuntimeException('ランキングが1件も取得できませんでした（ページ構造が変わった可能性があります）');
            }
        } catch (Throwable $e) {
            logMessage("エラー（{$category}）: " . $e->getMessage());
            $failed[] = $category;
            continue;
        }
        foreach ($items as $item) {
            $rows[] = [
                'collected_at' => $collectedAt,
                'category' => $category,
                'rank' => $item['rank'],
                'title' => $item['title'],
                'url' => $item['url'],
                'comments_per_hour' => $item['comments_per_hour'],
                'published_at' => normalizePublishedAt($item['published_text'], $now),
            ];
        }
    }

    if (!$rows) {
        logMessage('エラー: すべてのカテゴリーで取得に失敗したため、CSVは出力しません。');
        return 1;
    }
    try {
        writeCsv($output, $rows);
    } catch (Throwable $e) {
        logMessage('エラー: ' . $e->getMessage());
        return 1;
    }
    logMessage(count($rows) . "件を {$output} に出力しました。");

    // ランキングに載った記事（重複なし）のコメントを取得する。1記事で失敗しても他の記事は続ける
    $articles = [];
    foreach ($rows as $row) {
        $articles[$row['url']] ??= $row['title'];
    }
    $commentRows = [];
    $failedArticles = 0;
    $n = 0;
    foreach ($articles as $url => $title) {
        $n++;
        sleep(PAGE_WAIT_SECONDS);
        logMessage("コメント取得中 ({$n}/" . count($articles) . "): {$url}");
        try {
            $comments = collectComments($url, COMMENTS_PER_ARTICLE);
        } catch (Throwable $e) {
            logMessage("エラー（コメント）: {$url} " . $e->getMessage());
            $failedArticles++;
            continue;
        }
        foreach ($comments as $i => $c) {
            $commentRows[] = [
                'collected_at' => $collectedAt,
                'article_url' => $url,
                'title' => $title,
                'comment_rank' => $i + 1,
            ] + $c;
        }
    }
    try {
        writeCsv($commentOutput, $commentRows, COMMENT_CSV_COLUMNS);
    } catch (Throwable $e) {
        logMessage('エラー: ' . $e->getMessage());
        return 1;
    }
    logMessage(count($articles) . '記事・コメント' . count($commentRows) . "件を {$commentOutput} に出力しました。");

    if ($failed) {
        logMessage('取得に失敗したカテゴリー: ' . implode('、', $failed));
    }
    if ($failedArticles) {
        logMessage("コメント取得に失敗した記事: {$failedArticles}件");
    }
    return ($failed || $failedArticles) ? 1 : 0;
}

exit(main($argv));
