<?php
/**
 * ヤフコメランキング（総合）TOP40 を監視し、前回の監視時には無かった記事が
 * ランクインしたときだけ Discord に通知するスクリプト
 *
 * 通知内容: 順位・タイトル（記事へのリンク）・件/時
 *
 * 準備:
 *   1. notify_config.example.php を notify_config.php という名前でコピーし、
 *      Discord の Webhook URL を書き込む
 *   2. このファイル・yahoo_comment_ranking.php・notify_config.php を同じフォルダに置く
 *
 * 使い方:
 *   php yahoo_comment_ranking_notify.php            # 監視して通知
 *   php yahoo_comment_ranking_notify.php --dry-run  # Discord には送らず、送る内容を表示するだけ（記録も更新しない）
 *
 * Cron（ConoHa WING「サーバー管理 > ジョブスケジューラー」）例: 10分ごと
 *   /usr/bin/php /home/<ユーザー名>/yahoo_ranking/yahoo_comment_ranking_notify.php >> /home/<ユーザー名>/yahoo_ranking/notify.log 2>&1
 *
 * ・初回の実行では、その時点のランキングを記録するだけで通知はしない（40件いっせいに届くのを防ぐため）
 * ・取得や通知に失敗したときは記録を更新しないので、次回の監視で改めて通知される
 */

require_once __DIR__ . '/yahoo_comment_ranking.php';

// ==========================================
// 設定エリア
// ==========================================
const NOTIFY_TOP_N = 40;
const NOTIFY_CONFIG_FILE = __DIR__ . '/notify_config.php';
const NOTIFY_STATE_FILE = OUTPUT_DIR . '/notify_state.json';  // 前回の監視結果
const DISCORD_ITEMS_PER_MESSAGE = 10;                          // 1回の投稿に載せる記事数

// ==========================================
// 処理本体
// ==========================================
function loadPreviousUrls(): ?array
{
    if (!is_file(NOTIFY_STATE_FILE)) {
        return null; // 初回
    }
    $state = json_decode((string)file_get_contents(NOTIFY_STATE_FILE), true);
    if (!is_array($state) || !isset($state['urls']) || !is_array($state['urls'])) {
        throw new RuntimeException('前回の監視結果を読み込めません: ' . NOTIFY_STATE_FILE);
    }
    return $state['urls'];
}

function savePreviousUrls(array $urls, string $checkedAt): void
{
    $dir = dirname(NOTIFY_STATE_FILE);
    if (!is_dir($dir) && !mkdir($dir, 0755, true)) {
        throw new RuntimeException("保存先ディレクトリを作成できません: {$dir}");
    }
    $json = json_encode(['checked_at' => $checkedAt, 'urls' => array_values($urls)], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT);
    if (file_put_contents(NOTIFY_STATE_FILE, $json, LOCK_EX) === false) {
        throw new RuntimeException('監視結果を保存できません: ' . NOTIFY_STATE_FILE);
    }
}

function discordWebhookUrl(): string
{
    if (!is_file(NOTIFY_CONFIG_FILE)) {
        throw new RuntimeException('設定ファイルがありません: ' . NOTIFY_CONFIG_FILE . '（notify_config.example.php をコピーして作成してください）');
    }
    $config = require NOTIFY_CONFIG_FILE;
    $url = $config['discord_webhook_url'] ?? '';
    if (!preg_match('#^https://(discord|discordapp)\.com/api/webhooks/#', $url)) {
        throw new RuntimeException('Discord の Webhook URL が正しく設定されていません: ' . NOTIFY_CONFIG_FILE);
    }
    return $url;
}

/** Discord に1件投稿する。レート制限（429）のときは指定秒数待って1回だけやり直す */
function postToDiscord(string $webhookUrl, array $payload): void
{
    for ($attempt = 1; $attempt <= 2; $attempt++) {
        $ch = curl_init($webhookUrl);
        curl_setopt_array($ch, [
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => json_encode($payload, JSON_UNESCAPED_UNICODE),
            CURLOPT_HTTPHEADER => ['Content-Type: application/json'],
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => 30,
        ]);
        $body = curl_exec($ch);
        $status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $error = curl_error($ch);
        curl_close($ch);

        if ($status >= 200 && $status < 300) {
            return;
        }
        if ($status === 429 && $attempt === 1) {
            $retryAfter = (float)(json_decode((string)$body, true)['retry_after'] ?? 1);
            usleep((int)(min($retryAfter, 10) * 1000000));
            continue;
        }
        throw new RuntimeException("Discord への送信に失敗しました (HTTP {$status}) {$error} {$body}");
    }
}

/** 新しくランクインした記事を、Discord の投稿（埋め込み）に組み立てる */
function buildDiscordPayloads(array $items, string $checkedAt): array
{
    $payloads = [];
    $chunks = array_chunk($items, DISCORD_ITEMS_PER_MESSAGE);
    foreach ($chunks as $i => $chunk) {
        $lines = array_map(function ($item) {
            // タイトル中の [ ] はリンク表記を壊すので全角に置き換える
            $title = strtr($item['title'], ['[' => '［', ']' => '］']);
            $count = $item['comments_per_hour'] !== '' ? "　💬 {$item['comments_per_hour']}件/時" : '';
            return "**{$item['rank']}位** [{$title}]({$item['url']}){$count}";
        }, $chunk);
        $title = '🆕 ヤフコメランキング 新着ランクイン（' . count($items) . '件）';
        if (count($chunks) > 1) {
            $title .= ' ' . ($i + 1) . '/' . count($chunks);
        }
        $payloads[] = [
            'embeds' => [[
                'title' => $title,
                'url' => RANKING_URL,
                'description' => implode("\n\n", $lines),
                'color' => 0xFF4500,
                'footer' => ['text' => "{$checkedAt} 時点 / 前回の監視時にTOP" . NOTIFY_TOP_N . '外だった記事'],
            ]],
        ];
    }
    return $payloads;
}

function notifyMain(array $argv): int
{
    $dryRun = in_array('--dry-run', $argv, true);
    $checkedAt = (new DateTimeImmutable())->format('Y-m-d H:i');

    try {
        $webhookUrl = $dryRun ? '' : discordWebhookUrl();
        $items = collectRanking(RANKING_URL, NOTIFY_TOP_N);
        if (!$items) {
            throw new RuntimeException('ランキングが1件も取得できませんでした（ページ構造が変わった可能性があります）');
        }
        $currentUrls = array_map(fn($item) => $item['url'], $items);
        $previousUrls = loadPreviousUrls();

        if ($previousUrls === null) {
            logMessage('初回の実行のため、ランキングを記録するだけで通知はしません（' . count($items) . '件）。');
        } else {
            $previous = array_flip($previousUrls);
            $newItems = array_values(array_filter($items, fn($item) => !isset($previous[$item['url']])));
            if (!$newItems) {
                logMessage('新しくランクインした記事はありません。');
            } else {
                foreach (buildDiscordPayloads($newItems, $checkedAt) as $i => $payload) {
                    if ($dryRun) {
                        echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT), "\n";
                        continue;
                    }
                    if ($i > 0) {
                        sleep(1);
                    }
                    postToDiscord($webhookUrl, $payload);
                }
                logMessage(count($newItems) . '件の新着ランクインを' . ($dryRun ? '表示しました（--dry-run のため送信なし）。' : 'Discordに通知しました。'));
            }
        }

        if (!$dryRun) {
            savePreviousUrls($currentUrls, $checkedAt);
        }
        return 0;
    } catch (Throwable $e) {
        logMessage('エラー: ' . $e->getMessage());
        return 1;
    }
}

exit(notifyMain($argv));
