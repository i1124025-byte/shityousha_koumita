<?php
/**
 * Yahoo!ニュース ヤフコメランキング（総合）TOP50 を CSV に出力するスクリプト
 *
 * 出力項目: collected_at,rank,title,url,comments_per_hour,published_at
 *
 * 使い方:
 *   php yahoo_comment_ranking.php                 # data/yahoo_comment_ranking_YYYYMMDD.csv に出力
 *   php yahoo_comment_ranking.php /path/to/out.csv # 出力先を指定
 *
 * Cron（ConoHa WING ジョブスケジューラー）例: 毎朝7時
 *   /usr/bin/php /home/<ユーザー名>/scripts/yahoo_comment_ranking.php
 *
 * Yahoo!ニュースのクラス名は自動生成で頻繁に変わるため、要素の特定はクラス名ではなく
 * data-cl-params 属性（_cl_vmodule:ranking / _cl_position:N）とタグ構造で行っている。
 */

date_default_timezone_set('Asia/Tokyo');

// ==========================================
// 設定エリア
// ==========================================
const RANKING_URL = 'https://news.yahoo.co.jp/ranking/comment';
const TOP_N = 50;
const MAX_PAGES = 3;            // TOP_N に足りない場合に ?page=N を辿る上限
const PAGE_WAIT_SECONDS = 1;    // ページ取得間の待機（アクセス負荷軽減）
const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    . '(KHTML, like Gecko) Chrome/124.0 Safari/537.36';
const OUTPUT_DIR = __DIR__ . '/data';

const CSV_COLUMNS = ['collected_at', 'rank', 'title', 'url', 'comments_per_hour', 'published_at'];

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

function collectRanking(): array
{
    $byRank = [];
    for ($page = 1; $page <= MAX_PAGES && count($byRank) < TOP_N; $page++) {
        $url = $page === 1 ? RANKING_URL : RANKING_URL . '?page=' . $page;
        logMessage("取得中: {$url}");
        $items = parseRanking(fetchHtml($url));
        $added = 0;
        foreach ($items as $item) {
            if ($item['rank'] <= TOP_N && !isset($byRank[$item['rank']])) {
                $byRank[$item['rank']] = $item;
                $added++;
            }
        }
        if ($added === 0) {
            break; // 次ページが存在しない（同じ内容が返る）場合
        }
        if (count($byRank) < TOP_N) {
            sleep(PAGE_WAIT_SECONDS);
        }
    }
    ksort($byRank);
    return array_values($byRank);
}

function writeCsv(string $path, array $rows): void
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
    fputcsv($fp, CSV_COLUMNS, ',', '"', '');
    foreach ($rows as $row) {
        fputcsv($fp, array_map(fn($c) => $row[$c], CSV_COLUMNS), ',', '"', '');
    }
    fclose($fp);
}

function main(array $argv): int
{
    $now = new DateTimeImmutable();
    $output = $argv[1] ?? OUTPUT_DIR . '/yahoo_comment_ranking_' . $now->format('Ymd') . '.csv';

    try {
        $items = collectRanking();
        if (!$items) {
            throw new RuntimeException('ランキングが1件も取得できませんでした（ページ構造が変わった可能性があります）');
        }

        $collectedAt = $now->format('Y-m-d H:i:s');
        $rows = array_map(fn($item) => [
            'collected_at' => $collectedAt,
            'rank' => $item['rank'],
            'title' => $item['title'],
            'url' => $item['url'],
            'comments_per_hour' => $item['comments_per_hour'],
            'published_at' => normalizePublishedAt($item['published_text'], $now),
        ], $items);

        writeCsv($output, $rows);
        logMessage(count($rows) . "件を {$output} に出力しました。");
        if (count($rows) < TOP_N) {
            logMessage('警告: TOP' . TOP_N . 'に満たない件数でした。');
        }
        return 0;
    } catch (Throwable $e) {
        logMessage('エラー: ' . $e->getMessage());
        return 1;
    }
}

exit(main($argv));
