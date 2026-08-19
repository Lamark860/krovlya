<?php
/**
 * Приём заявок с лендингов — версия под виртуальный хостинг (PHP 8 + MySQL).
 * Порт `api/server.ts` (Bun + SQLite) один в один: те же правила валидации,
 * те же поля, те же ответы. Фронтенд не правился.
 *
 * Порядок действий на каждую заявку тот же, что и в оригинале: сначала запись
 * в базу, потом доставка. Почта и мессенджеры отваливаются, база — нет;
 * заявка не должна теряться из-за того, что у почтового сервера плохой день.
 *
 * Точка входа одна: .htaccess заворачивает сюда весь /api/. Так на shared-хостинге
 * надёжнее, чем россыпь файлов, — одно правило переписывания вместо трёх.
 */

declare(strict_types=1);

// Конфиг лежит ВНЕ webroot и вне git: репозиторий публичный, а там пароль от базы,
// токен бота и адрес получателя. Ожидаемое место — /var/www/<логин>/data/config/krovlya.php.
//
// Ищем его подъёмом вверх, а не по фиксированной глубине: где именно окажется корень
// сайта, зависит от того, как его заведут в панели, и ошибиться там легко.
$configPath = getenv('KROVLYA_CONFIG') ?: '';
if ($configPath === '') {
    $dir = __DIR__;
    for ($i = 0; $i < 5; $i++) {
        $dir = dirname($dir);
        if (is_file("$dir/config/krovlya.php")) { $configPath = "$dir/config/krovlya.php"; break; }
    }
}
if ($configPath === '' || !is_file($configPath)) {
    http_response_code(500);
    header('content-type: application/json; charset=utf-8');
    echo json_encode(['error' => 'Сервис временно недоступен'], JSON_UNESCAPED_UNICODE);
    error_log("krovlya: конфиг не найден, искали config/krovlya.php вверх от " . __DIR__);
    exit;
}
$config = require $configPath;

const TOKEN_TTL_HOURS = 24;
const RATE_LIMIT_MAX = 5;
const RATE_LIMIT_WINDOW_MIN = 10;
const MIN_FILL_MS = 2000; // быстрее двух секунд форму заполняет только бот

function json_out(array $data, int $status = 200): never
{
    http_response_code($status);
    header('content-type: application/json; charset=utf-8');
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function db(array $config): PDO
{
    static $pdo = null;
    if ($pdo instanceof PDO) return $pdo;

    $dsn = sprintf('mysql:host=%s;dbname=%s;charset=utf8mb4', $config['db_host'], $config['db_name']);
    $pdo = new PDO($dsn, $config['db_user'], $config['db_pass'], [
        PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES   => false,
    ]);
    return $pdo;
}

function client_ip(): string
{
    // За nginx хостинга реальный адрес приезжает в заголовке; берём первый в цепочке.
    $forwarded = $_SERVER['HTTP_X_FORWARDED_FOR'] ?? '';
    if ($forwarded !== '') {
        $first = trim(explode(',', $forwarded)[0]);
        if ($first !== '') return substr($first, 0, 45);
    }
    return substr($_SERVER['REMOTE_ADDR'] ?? 'unknown', 0, 45);
}

/**
 * В Bun-версии счётчик жил в памяти процесса. PHP умирает после каждого запроса,
 * поэтому окно храним в базе. Заодно переживает перезапуск и не сбрасывается.
 */
function rate_limited(PDO $pdo, string $ip): bool
{
    $pdo->prepare('DELETE FROM rate_hits WHERE hit_at < (NOW() - INTERVAL ? MINUTE)')
        ->execute([RATE_LIMIT_WINDOW_MIN]);

    $pdo->prepare('INSERT INTO rate_hits (ip, hit_at) VALUES (?, NOW())')->execute([$ip]);

    $stmt = $pdo->prepare(
        'SELECT COUNT(*) c FROM rate_hits WHERE ip = ? AND hit_at >= (NOW() - INTERVAL ? MINUTE)'
    );
    $stmt->execute([$ip, RATE_LIMIT_WINDOW_MIN]);
    return (int) $stmt->fetch()['c'] > RATE_LIMIT_MAX;
}

const CHANNELS = ['telegram', 'max', 'phone'];

/** Возвращает текст ошибки или null. Валидируем на сервере: клиенту веры нет. */
function validate(array $body): ?string
{
    $name    = trim((string) ($body['name'] ?? ''));
    $contact = trim((string) ($body['contact'] ?? ''));
    $channel = trim((string) ($body['channel'] ?? ''));

    if ((string) ($body['company'] ?? '') !== '') return 'spam';              // honeypot
    if ((int) ($body['elapsed'] ?? 0) < MIN_FILL_MS) return 'spam';          // слишком быстро
    if (($body['consent'] ?? null) !== true) return 'Нужно согласие на обработку данных';

    // Имя необязательно: в попапе «в 1 клик» его не спрашивают — там только телефон.
    if ($name !== '' && (mb_strlen($name) < 2 || mb_strlen($name) > 60)) return 'Проверьте имя';
    if (!in_array($channel, CHANNELS, true)) return 'Выберите способ связи';

    if ($channel === 'telegram' && preg_match('/^@[\w\d_]{4,32}$/u', $contact)) return null;

    $digits = preg_replace('/\D/', '', $contact);
    if (strlen($digits) < 10 || strlen($digits) > 11) return 'Проверьте номер телефона';
    return null;
}

const CHANNEL_LABELS = ['telegram' => 'Telegram', 'max' => 'MAX', 'phone' => 'Телефон'];

function lead_text(array $lead, int $id): string
{
    $text  = "Заявка №$id — " . ($lead['page'] ?? 'сайт') . "\n";
    $text .= 'Имя: ' . ($lead['name'] !== '' ? $lead['name'] : 'не указано') . "\n";
    $text .= 'Связь: ' . (CHANNEL_LABELS[$lead['channel']] ?? $lead['channel']) . ' — ' . $lead['contact'] . "\n";
    if ($lead['geo']) $text .= 'Гео: ' . $lead['geo'] . "\n";
    // «Детали», а не «Откуда»: сюда приезжает и товар из карточки,
    // и кейс портфолио, и пожелание «перезвонить в течение часа».
    if ($lead['source']) $text .= 'Детали: ' . $lead['source'] . "\n";
    if ($lead['magnet']) $text .= 'Лид-магнит: ' . $lead['magnet'] . "\n";
    if ($lead['quiz'])   $text .= "\nОтветы квиза:\n" . $lead['quiz'];
    if ($lead['utm'])    $text .= "\nUTM: " . $lead['utm'];
    return $text;
}

function send_telegram(array $config, string $text): bool
{
    if (empty($config['telegram_token']) || empty($config['telegram_chat_id'])) return false;

    $url  = "https://api.telegram.org/bot{$config['telegram_token']}/sendMessage";
    $body = json_encode(['chat_id' => $config['telegram_chat_id'], 'text' => $text], JSON_UNESCAPED_UNICODE);

    if (function_exists('curl_init')) {
        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_POST           => true,
            CURLOPT_POSTFIELDS     => $body,
            CURLOPT_HTTPHEADER     => ['content-type: application/json'],
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT        => 10,
        ]);
        $ok = curl_exec($ch) !== false && curl_getinfo($ch, CURLINFO_RESPONSE_CODE) === 200;
        curl_close($ch);
        if (!$ok) error_log('krovlya: Telegram не принял заявку');
        return $ok;
    }

    $context = stream_context_create(['http' => [
        'method'        => 'POST',
        'header'        => "content-type: application/json\r\n",
        'content'       => $body,
        'timeout'       => 10,
        'ignore_errors' => true,
    ]]);
    return @file_get_contents($url, false, $context) !== false;
}

/**
 * Письмо шлём штатным sendmail хостинга: у него есть PTR и SPF домена,
 * поэтому оно не падает в спам — ради этого и переезжали с VPS.
 */
function send_mail(array $config, string $text, int $id): bool
{
    if (empty($config['mail_to']) || empty($config['mail_from'])) return false;

    $subject = "Заявка №$id с сайта";
    $headers = [
        'From: ' . $config['mail_from'],
        'Content-Type: text/plain; charset=UTF-8',
        'Content-Transfer-Encoding: 8bit',
        'MIME-Version: 1.0',
    ];
    // Кириллица в теме письма живёт только в MIME-кодировке, иначе поедет в кракозябры.
    $encoded = '=?UTF-8?B?' . base64_encode($subject) . '?=';

    $ok = @mail($config['mail_to'], $encoded, $text, implode("\r\n", $headers));
    if (!$ok) error_log('krovlya: sendmail не принял заявку');
    return $ok;
}

// --- Маршрутизация -------------------------------------------------------
// .htaccess отдаёт сюда весь /api/, исходный путь — в REQUEST_URI.
$path = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/';
$path = preg_replace('#^/api#', '', $path);
$path = rtrim($path, '/') ?: '/';
$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';

if ($path === '/health') {
    json_out(['ok' => true]);
}

if ($path === '/lead' && $method === 'POST') {
    try {
        $pdo = db($config);
    } catch (Throwable $e) {
        error_log('krovlya: база недоступна — ' . $e->getMessage());
        json_out(['error' => 'Сервис временно недоступен, позвоните нам'], 503);
    }

    $ip = client_ip();
    if (rate_limited($pdo, $ip)) {
        json_out(['error' => 'Слишком много заявок, попробуйте позже'], 429);
    }

    $body = json_decode(file_get_contents('php://input') ?: '', true);
    if (!is_array($body)) {
        json_out(['error' => 'Некорректный запрос'], 400);
    }

    $problem = validate($body);
    if ($problem === 'spam') json_out(['ok' => true]);   // боту отвечаем как обычно
    if ($problem !== null)   json_out(['error' => $problem], 400);

    $lead = [
        'created_at' => gmdate('Y-m-d\TH:i:s.v\Z'),
        'name'       => trim((string) ($body['name'] ?? '')),
        'contact'    => trim((string) ($body['contact'] ?? '')),
        'channel'    => (string) $body['channel'],
        'page'       => isset($body['page'])   ? (string) $body['page'] : null,
        'geo'        => isset($body['geo'])    ? (string) $body['geo'] : null,
        'source'     => isset($body['source']) ? mb_substr((string) $body['source'], 0, 200) : null,
        'magnet'     => isset($body['magnet']) ? (string) $body['magnet'] : null,
        'quiz'       => isset($body['quiz']) ? json_encode($body['quiz'], JSON_UNESCAPED_UNICODE) : null,
        'utm'        => isset($body['utm'])  ? json_encode($body['utm'],  JSON_UNESCAPED_UNICODE) : null,
        'ip'         => $ip,
    ];

    $stmt = $pdo->prepare(
        'INSERT INTO leads (created_at, name, contact, channel, page, geo, source, magnet, quiz, utm, ip)
         VALUES (:created_at, :name, :contact, :channel, :page, :geo, :source, :magnet, :quiz, :utm, :ip)'
    );
    $stmt->execute($lead);
    $id = (int) $pdo->lastInsertId();

    $text      = lead_text($lead, $id);
    $delivered = send_telegram($config, $text);
    $delivered = send_mail($config, $text, $id) || $delivered;
    if ($delivered) {
        $pdo->prepare('UPDATE leads SET delivered = 1 WHERE id = ?')->execute([$id]);
    } else {
        error_log("krovlya: доставка не настроена или не прошла, заявка №$id только в базе");
    }

    // Лид-магнит выдаём одноразовой ссылкой: файл не должен утечь в индекс
    $download = null;
    if ($lead['magnet']) {
        $token   = bin2hex(random_bytes(16));
        $expires = gmdate('Y-m-d H:i:s', time() + TOKEN_TTL_HOURS * 3600);
        $pdo->prepare('INSERT INTO downloads (token, magnet, expires_at) VALUES (?, ?, ?)')
            ->execute([$token, $lead['magnet'], $expires]);
        $download = "/api/download?token=$token";
    }

    json_out(['ok' => true, 'download' => $download]);
}

if ($path === '/download') {
    try {
        $pdo = db($config);
    } catch (Throwable $e) {
        error_log('krovlya: база недоступна — ' . $e->getMessage());
        json_out(['error' => 'Сервис временно недоступен'], 503);
    }

    $token = (string) ($_GET['token'] ?? '');
    $stmt  = $pdo->prepare('SELECT * FROM downloads WHERE token = ?');
    $stmt->execute([$token]);
    $row = $stmt->fetch();

    if (!$row) json_out(['error' => 'Ссылка недействительна'], 404);
    if (new DateTime($row['expires_at'], new DateTimeZone('UTC')) < new DateTime('now', new DateTimeZone('UTC'))) {
        json_out(['error' => 'Ссылка устарела'], 410);
    }

    // Файлы лежат вне webroot: скачать можно только по токену, прямой ссылки нет.
    $name = preg_replace('/[^a-z0-9_-]/i', '', $row['magnet']);
    $file = rtrim($config['files_dir'], '/') . "/$name.pdf";
    if (!is_file($file)) json_out(['error' => 'Файл пока не готов'], 404);

    $pdo->prepare('UPDATE downloads SET used_at = NOW() WHERE token = ?')->execute([$token]);

    header('content-type: application/pdf');
    header('content-length: ' . filesize($file));
    header("content-disposition: attachment; filename=\"$name.pdf\"");
    readfile($file);
    exit;
}

json_out(['error' => 'Not found'], 404);
