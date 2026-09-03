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

// Ошибки — только в лог, никогда на экран. На хостинге display_errors может быть включён,
// и тогда при любом сбое в браузер уезжает стектрейс с путями. Проверено на живом сервере:
// без этого отсутствующая таблица отдавалась пользователю как текст ошибки, да ещё с кодом 200.
ini_set('display_errors', '0');
ini_set('log_errors', '1');

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

set_exception_handler(function (Throwable $e): void {
    error_log('krovlya: необработанная ошибка — ' . $e->getMessage());
    if (!headers_sent()) {
        http_response_code(500);
        header('content-type: application/json; charset=utf-8');
    }
    echo json_encode(['error' => 'Сервис временно недоступен, позвоните нам'], JSON_UNESCAPED_UNICODE);
});

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

/** Имена страниц: «Заявка №10 — Полы» читается быстрее, чем «Заявка №10 — /poly». */
const PAGE_LABELS = [
    '/'               => 'Главная',
    '/poly'           => 'Полы: ламинат и кварцвинил',
    '/plitka'         => 'Плитка и керамогранит',
    '/catalog'        => 'Каталог полов',
    '/catalog-plitka' => 'Каталог плитки',
    '/quiz'           => 'Квиз: полы',
    '/quiz-plitka'    => 'Квиз: плитка',
    '/thanks'         => 'Страница «Спасибо»',
];

function page_label(?string $page): string
{
    $path = rtrim((string) $page, '/') ?: '/';
    return PAGE_LABELS[$path] ?? ((string) $page !== '' ? (string) $page : 'сайт');
}

const MAX_QUIZ_LINES = 40;

/**
 * Ответы квиза приезжают уже по-русски, готовыми строками: собирает их фронтенд
 * (`describeQuiz` в `site/src/data/quiz.ts`) — там же, где лежат сами вопросы,
 * иначе словарь подписей пришлось бы держать ещё и здесь, и в Bun-версии.
 * Здесь остаётся только подрезать: текст пришёл от клиента.
 */
function quiz_lines(mixed $value): array
{
    if (!is_array($value)) return [];

    $lines = [];
    foreach (array_slice($value, 0, MAX_QUIZ_LINES) as $line) {
        if (!is_scalar($line)) continue;
        $lines[] = mb_substr(trim((string) preg_replace('/\s+/u', ' ', (string) $line)), 0, 200);
    }
    return $lines;
}

const UTM_LABELS = [
    'utm_campaign' => 'Кампания',
    'utm_content'  => 'Объявление',
    'utm_term'     => 'Ключевая фраза',
    'yclid'        => 'Клик в Директе (yclid)',
];

/**
 * Метки визита строками. Всё, чему не нашлось подписи, уходит одной строкой:
 * там город из ссылки и параметры выборки каталога — по ним видно, что человек
 * смотрел до заявки.
 */
function utm_lines(mixed $value): array
{
    if (!is_array($value)) return [];

    $text = static function (string $key) use ($value): string {
        $raw = $value[$key] ?? '';
        return is_scalar($raw) ? mb_substr(trim((string) $raw), 0, 200) : '';
    };

    $lines  = [];
    $source = implode(' / ', array_filter([$text('utm_source'), $text('utm_medium')], static fn ($v) => $v !== ''));
    if ($source !== '') $lines[] = 'Источник: ' . $source;
    foreach (UTM_LABELS as $key => $label) {
        if ($text($key) !== '') $lines[] = "$label: " . $text($key);
    }

    // `etext` — служебный токен Яндекса на пол-экрана: в базе остаётся, в письме мешает.
    $skip = array_merge(['utm_source', 'utm_medium', 'etext'], array_keys(UTM_LABELS));
    $rest = [];
    foreach (array_keys($value) as $key) {
        if (in_array((string) $key, $skip, true) || $text((string) $key) === '') continue;
        $rest[] = $key . '=' . mb_substr($text((string) $key), 0, 60);
        if (count($rest) >= 10) break;
    }
    if ($rest) $lines[] = 'Ещё из ссылки: ' . implode(', ', $rest);

    return $lines;
}

/** Текст заявки — один и тот же в Telegram и в письме. */
function lead_text(array $lead, int $id, array $body): string
{
    // Фронтенд мог быть старым (страница из кэша браузера) — тогда печатаем машинные
    // ответы, как раньше: показать менеджеру JSON лучше, чем потерять разбор квиза.
    $quiz = quiz_lines($body['quizText'] ?? null);
    if (!$quiz && $lead['quiz']) $quiz = [(string) $lead['quiz']];
    $utm = utm_lines($body['utm'] ?? null);

    $lines = [
        "Заявка №$id — " . page_label($lead['page']),
        'Имя: ' . ($lead['name'] !== '' ? $lead['name'] : 'не указано'),
        'Связь: ' . (CHANNEL_LABELS[$lead['channel']] ?? $lead['channel']) . ' — ' . $lead['contact'],
    ];
    if ($lead['geo']) $lines[] = 'Гео: ' . $lead['geo'];
    // «Детали», а не «Откуда»: сюда приезжает и товар из карточки,
    // и кейс портфолио, и пожелание «перезвонить в течение часа».
    if ($lead['source']) $lines[] = 'Детали: ' . $lead['source'];
    if ($lead['magnet']) $lines[] = 'Лид-магнит: ' . $lead['magnet'];
    if ($quiz) $lines = array_merge($lines, ['', 'Ответы квиза:'], $quiz);
    if ($utm)  $lines = array_merge($lines, ['', 'Откуда пришёл:'], $utm);

    return implode("\n", $lines);
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
 *
 * Получателей несколько: ящик клиента и ящик Максима. Каждому — отдельное
 * письмо, а не один конверт со списком. Причина простая: Яндекс и mail.ru
 * заворачивают письмо на всех адресатов, если хоть один им не понравился,
 * и тогда заявку не увидит никто. Отдельными письмами теряется максимум одно.
 */
function mail_recipients(array $config): array
{
    $raw = $config['mail_to'] ?? '';
    // В конфиге допустимы и строка «a@x, b@y», и массив — так проще править руками.
    $list = is_array($raw) ? $raw : preg_split('/[,;\s]+/', (string) $raw);
    $clean = [];
    foreach ($list as $address) {
        $address = trim((string) $address);
        if ($address !== '' && filter_var($address, FILTER_VALIDATE_EMAIL)) $clean[] = $address;
    }
    return array_values(array_unique($clean));
}

function send_mail(array $config, string $text, int $id, string $subject): bool
{
    $recipients = mail_recipients($config);
    if (!$recipients || empty($config['mail_from'])) return false;

    $headers = [
        'From: ' . $config['mail_from'],
        'Content-Type: text/plain; charset=UTF-8',
        'Content-Transfer-Encoding: 8bit',
        'MIME-Version: 1.0',
    ];
    // Кириллица в теме письма живёт только в MIME-кодировке, иначе поедет в кракозябры.
    $encoded = '=?UTF-8?B?' . base64_encode($subject) . '?=';

    $delivered = false;
    foreach ($recipients as $address) {
        if (@mail($address, $encoded, $text, implode("\r\n", $headers))) {
            $delivered = true;
        } else {
            error_log("krovlya: sendmail не принял заявку №$id для $address");
        }
    }
    return $delivered;
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
    // Под try — вся работа с базой целиком, а не только подключение: упасть может и запрос
    // (нет таблицы, кончилось место, отвалился сокет), а человек у формы должен увидеть
    // осмысленный ответ, а не пустую кнопку.
    try {
    $pdo = db($config);

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

    $text      = lead_text($lead, $id, $body);
    // Тема письма — не «Заявка №10 с сайта», а с направлением и телефоном:
    // в списке входящих сразу видно, кому и о чём звонить.
    $subject   = "Заявка №$id — " . page_label($lead['page']) . ', ' . $lead['contact'];
    $delivered = send_telegram($config, $text);
    $delivered = send_mail($config, $text, $id, $subject) || $delivered;
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

    } catch (PDOException $e) {
        error_log('krovlya: заявка не сохранена — ' . $e->getMessage());
        json_out(['error' => 'Сервис временно недоступен, позвоните нам'], 503);
    }
}

if ($path === '/download') {
    try {
    $pdo = db($config);

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

    } catch (PDOException $e) {
        error_log('krovlya: выдача лид-магнита не прошла — ' . $e->getMessage());
        json_out(['error' => 'Сервис временно недоступен'], 503);
    }
}

json_out(['error' => 'Not found'], 404);
