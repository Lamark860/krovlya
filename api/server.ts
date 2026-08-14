/**
 * Приём заявок с лендингов. Голый Bun.serve + bun:sqlite, без зависимостей.
 *
 * Порядок действий на каждую заявку: сначала запись в базу, потом доставка.
 * Почта и мессенджеры отваливаются, база — нет; заявка не должна теряться из-за
 * того, что у почтового сервера плохой день.
 *
 * Каналы доставки: Telegram работает сразу, e-mail появится вместе с доменом
 * и почтовым ящиком (см. TODO в notify()).
 */
import { Database } from "bun:sqlite";
import { existsSync } from "node:fs";

const PORT = Number(Bun.env.PORT ?? 8105);
const DB_PATH = Bun.env.DB_PATH ?? "./data/leads.sqlite";
const FILES_DIR = Bun.env.FILES_DIR ?? "./files";
const TOKEN_TTL_HOURS = 24;

const RATE_LIMIT = { max: 5, windowMs: 10 * 60 * 1000 };
const MIN_FILL_MS = 2000; // быстрее двух секунд форму заполняет только бот

const db = new Database(DB_PATH, { create: true });
db.exec("PRAGMA journal_mode = WAL");
db.exec(`
  CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    name TEXT NOT NULL,
    contact TEXT NOT NULL,
    channel TEXT NOT NULL,
    page TEXT,
    geo TEXT,
    magnet TEXT,
    quiz TEXT,
    utm TEXT,
    ip TEXT,
    delivered INTEGER NOT NULL DEFAULT 0
  );
  CREATE TABLE IF NOT EXISTS downloads (
    token TEXT PRIMARY KEY,
    magnet TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
  );
`);

/**
 * Догоняем схему на живой базе: CREATE TABLE IF NOT EXISTS новые колонки
 * не добавляет, а база на сервере уже с заявками.
 */
const columns = (db.query("PRAGMA table_info(leads)").all() as { name: string }[])
  .map((c) => c.name);
if (!columns.includes("source")) db.exec("ALTER TABLE leads ADD COLUMN source TEXT");

const insertLead = db.query(`
  INSERT INTO leads (created_at, name, contact, channel, page, geo, source, magnet, quiz, utm, ip)
  VALUES ($created_at, $name, $contact, $channel, $page, $geo, $source, $magnet, $quiz, $utm, $ip)
  RETURNING id
`);
const markDelivered = db.query("UPDATE leads SET delivered = 1 WHERE id = ?");
const insertToken = db.query(
  "INSERT INTO downloads (token, magnet, expires_at) VALUES (?, ?, ?)",
);
const findToken = db.query("SELECT * FROM downloads WHERE token = ?");
const useToken = db.query("UPDATE downloads SET used_at = ? WHERE token = ?");

const hits = new Map<string, number[]>();

function rateLimited(ip: string) {
  const now = Date.now();
  const recent = (hits.get(ip) ?? []).filter((t) => now - t < RATE_LIMIT.windowMs);
  recent.push(now);
  hits.set(ip, recent);
  return recent.length > RATE_LIMIT.max;
}

const CHANNELS = new Set(["telegram", "max", "phone"]);

/** Возвращает текст ошибки или null. Валидируем на сервере: клиенту веры нет. */
function validate(body: Record<string, unknown>): string | null {
  const name = String(body.name ?? "").trim();
  const contact = String(body.contact ?? "").trim();
  const channel = String(body.channel ?? "").trim();

  if (String(body.company ?? "")) return "spam";              // honeypot
  if (Number(body.elapsed ?? 0) < MIN_FILL_MS) return "spam"; // слишком быстро
  if (body.consent !== true) return "Нужно согласие на обработку данных";
  // Имя необязательно: в попапе «в 1 клик» его не спрашивают — там только телефон.
  if (name && (name.length < 2 || name.length > 60)) return "Проверьте имя";
  if (!CHANNELS.has(channel)) return "Выберите способ связи";

  const digits = contact.replace(/\D/g, "");
  if (channel === "telegram" && /^@[\w\d_]{4,32}$/.test(contact)) return null;
  if (digits.length < 10 || digits.length > 11) return "Проверьте номер телефона";
  return null;
}

const CHANNEL_LABELS: Record<string, string> = {
  telegram: "Telegram",
  max: "MAX",
  phone: "Телефон",
};

async function notify(lead: Record<string, unknown>, id: number): Promise<boolean> {
  const quiz = lead.quiz ? `\n\nОтветы квиза:\n${lead.quiz}` : "";
  const utm = lead.utm ? `\n\nUTM: ${lead.utm}` : "";
  const text =
    `Заявка №${id} — ${lead.page ?? "сайт"}\n` +
    `Имя: ${lead.name || "не указано"}\n` +
    `Связь: ${CHANNEL_LABELS[String(lead.channel)]} — ${lead.contact}\n` +
    (lead.geo ? `Гео: ${lead.geo}\n` : "") +
    // «Детали», а не «Откуда»: сюда приезжает и товар из карточки,
    // и кейс портфолио, и пожелание «перезвонить в течение часа».
    (lead.source ? `Детали: ${lead.source}\n` : "") +
    (lead.magnet ? `Лид-магнит: ${lead.magnet}\n` : "") +
    quiz + utm;

  const token = Bun.env.TELEGRAM_TOKEN;
  const chat = Bun.env.TELEGRAM_CHAT_ID;
  if (!token || !chat) {
    console.log("[lead] доставка не настроена, заявка только в базе:\n" + text);
    return false;
  }

  try {
    const response = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ chat_id: chat, text }),
    });
    if (!response.ok) throw new Error(`telegram ${response.status}`);
    return true;
  } catch (error) {
    console.error("[lead] Telegram не принял, заявка сохранена в базе:", error);
    return false;
  }
  // TODO: письмо на почту Максима и клиента — как только будет домен и SMTP-ящик.
}

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });

Bun.serve({
  port: PORT,
  async fetch(request, server) {
    const url = new URL(request.url);
    const ip = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim()
      ?? server.requestIP(request)?.address
      ?? "unknown";

    // nginx проксирует /api/ вместе с префиксом, поэтому принимаем оба варианта
    const path = url.pathname.replace(/^\/api/, "") || "/";

    if (path === "/health") return json({ ok: true });

    if (path === "/lead" && request.method === "POST") {
      if (rateLimited(ip)) return json({ error: "Слишком много заявок, попробуйте позже" }, 429);

      let body: Record<string, unknown>;
      try {
        body = await request.json();
      } catch {
        return json({ error: "Некорректный запрос" }, 400);
      }

      const problem = validate(body);
      if (problem === "spam") return json({ ok: true });  // боту отвечаем как обычно
      if (problem) return json({ error: problem }, 400);

      const lead = {
        created_at: new Date().toISOString(),
        name: String(body.name).trim(),
        contact: String(body.contact).trim(),
        channel: String(body.channel),
        page: body.page ? String(body.page) : null,
        geo: body.geo ? String(body.geo) : null,
        source: body.source ? String(body.source).slice(0, 200) : null,
        magnet: body.magnet ? String(body.magnet) : null,
        quiz: body.quiz ? JSON.stringify(body.quiz) : null,
        utm: body.utm ? JSON.stringify(body.utm) : null,
        ip,
      };

      const row = insertLead.get({
        $created_at: lead.created_at, $name: lead.name, $contact: lead.contact,
        $channel: lead.channel, $page: lead.page, $geo: lead.geo, $source: lead.source,
        $magnet: lead.magnet, $quiz: lead.quiz, $utm: lead.utm, $ip: lead.ip,
      }) as { id: number };

      if (await notify(lead, row.id)) markDelivered.run(row.id);

      // Лид-магнит выдаём одноразовой ссылкой: файл не должен утекать в индекс
      let download: string | null = null;
      if (lead.magnet) {
        const token = crypto.randomUUID();
        const expires = new Date(Date.now() + TOKEN_TTL_HOURS * 3600_000).toISOString();
        insertToken.run(token, lead.magnet, expires);
        download = `/api/download?token=${token}`;
      }

      return json({ ok: true, download });
    }

    if (path === "/download") {
      const token = url.searchParams.get("token") ?? "";
      const row = findToken.get(token) as
        { token: string; magnet: string; expires_at: string; used_at: string | null } | null;

      if (!row) return json({ error: "Ссылка недействительна" }, 404);
      if (new Date(row.expires_at) < new Date()) return json({ error: "Ссылка устарела" }, 410);

      const path = `${FILES_DIR}/${row.magnet.replace(/[^a-z0-9_-]/gi, "")}.pdf`;
      if (!existsSync(path)) return json({ error: "Файл пока не готов" }, 404);

      useToken.run(new Date().toISOString(), token);
      return new Response(Bun.file(path), {
        headers: { "content-disposition": `attachment; filename="${row.magnet}.pdf"` },
      });
    }

    return json({ error: "Not found" }, 404);
  },
});

console.log(`API заявок слушает :${PORT}, база ${DB_PATH}`);
