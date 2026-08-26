/**
 * Событийная аналитика для Яндекс.Метрики. По ТЗ Артура от 26.08
 * (`_local_docs/14_TZ_METRIKA.md`).
 *
 * Зачем отдельным модулем, а не строчками по компонентам: Мастер кампаний
 * Директа оптимизируется на цель `lead_form_success`, и если она хоть раз
 * уедет по клику на кнопку вместо ответа сервера — алгоритм будет учиться
 * на несуществующих заявках. Такую вещь держим в одном месте, где видно
 * все точки отправки сразу.
 *
 * Модуль импортируется из Base.astro (один раз на страницу) и из скриптов форм.
 * Vite сводит его в общий чанк, побочные эффекты выполняются однократно —
 * на всякий случай подстрахованы флагом `started`.
 */

export const METRIKA_ID = 111958532;

declare global {
  interface Window {
    ym?: (id: number, action: string, ...rest: unknown[]) => void;
  }
}

/** Цели, на которые Директ имеет право оптимизироваться. Остальное — микрособытия. */
export type Goal =
  | "lead_form_success" | "phone_click" | "messenger_click"
  | "quiz_lead_success" | "calculator_lead_success";

type Params = Record<string, unknown>;

/**
 * Отправка события. Если счётчик не загрузился (блокировщик, обрыв сети),
 * сценарий пользователя продолжается как ни в чём не бывало — молча роняем
 * аналитику, а не форму.
 */
export function trackMetrika(eventName: string, params: Params = {}): void {
  if (typeof window.ym !== "function") {
    console.warn("[Metrika] Функция ym недоступна:", eventName);
    return;
  }
  window.ym(METRIKA_ID, "reachGoal", eventName, { page: location.pathname, ...params });
}

/**
 * То же, но с ожиданием доставки. Нужно там, где сразу после события уходит
 * переход: форма отправляет заявку и уводит на «Спасибо», и без ожидания
 * запрос к Метрике не успевает уйти — цель теряется ровно на самом важном шаге.
 *
 * Ждём не дольше `timeout`: подвесить человека у отправленной формы
 * ради статистики нельзя.
 */
export function trackAndWait(eventName: string, params: Params = {}, timeout = 700): Promise<void> {
  if (typeof window.ym !== "function") {
    console.warn("[Metrika] Функция ym недоступна:", eventName);
    return Promise.resolve();
  }
  return new Promise<void>((resolve) => {
    let done = false;
    const finish = () => { if (!done) { done = true; resolve(); } };
    setTimeout(finish, timeout);
    window.ym!(METRIKA_ID, "reachGoal", eventName, { page: location.pathname, ...params }, finish);
  });
}

/** Направление посадочной — уезжает в параметр `service` целевых событий. */
export function serviceOf(): string {
  return location.pathname.includes("plitka") ? "plitka" : "poly";
}

// --- Рекламные метки -----------------------------------------------------
// Человек приходит по объявлению, а заявку оставляет через неделю с закладки.
// Без хранилища метка теряется, и Директ не понимает, какая кампания дала заявку.

const TRACKING = ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "yclid"] as const;
const TRACKING_TTL_DAYS = 90;
const STORE_KEY = "tracking_params";

type Tracking = { saved: Record<string, string>; at: number };

function readTracking(): Record<string, string> {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Tracking;
    const age = (Date.now() - parsed.at) / 86_400_000;
    if (age > TRACKING_TTL_DAYS) {
      localStorage.removeItem(STORE_KEY);
      return {};
    }
    return parsed.saved ?? {};
  } catch {
    return {};              // приватный режим или битый JSON — не повод ломать страницу
  }
}

/**
 * Метки визита. Если в адресе есть хоть одна — берём набор целиком и забываем
 * прежний: человек пришёл по новому объявлению, и заявка принадлежит ему,
 * а не кампании двухмесячной давности. Мержить нельзя — иначе к свежему
 * переходу прилипнет старая `utm_campaign`, и отчёт покажет не ту кампанию.
 * Если меток в адресе нет — отдаём сохранённые: это и есть отложенная заявка.
 */
export function trackingParams(): Record<string, string> {
  const fresh: Record<string, string> = {};
  const url = new URLSearchParams(location.search);
  for (const key of TRACKING) {
    const value = url.get(key);
    if (value) fresh[key] = value.slice(0, 200);
  }
  if (!Object.keys(fresh).length) return readTracking();

  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({ saved: fresh, at: Date.now() } satisfies Tracking));
  } catch { /* хранилище недоступно — метки доживут до конца страницы */ }
  return fresh;
}

/**
 * Раскладывает метки по скрытым полям форм. Заявка уходит JSON-ом, поля читаются
 * из него же — так значение, которое видно в разметке, и значение, которое уехало
 * менеджеру, не могут разойтись.
 */
function fillTrackingFields(root: ParentNode = document): void {
  const params = trackingParams();
  for (const key of TRACKING) {
    const value = params[key] ?? "";
    root.querySelectorAll<HTMLInputElement>(`[data-utm-field="${key}"]`).forEach((field) => {
      field.value = value;
    });
  }
}

/** Публичная обёртка: формы квиза рисуются на лету и просят заполнить свои поля. */
export function fillTracking(root: ParentNode = document): void {
  fillTrackingFields(root);
}

// --- Формы ---------------------------------------------------------------

export type FormMeta = { form_id: string; placement: string; lead_type: string };

export function formMeta(form: HTMLFormElement): FormMeta {
  return {
    form_id: form.dataset.formId || "unknown_form",
    placement: form.dataset.placement || "unknown",
    lead_type: form.dataset.leadType || "callback",
  };
}

/** Успешная заявка — единственная цель, ради которой всё это затевалось. */
export function leadSuccess(form: HTMLFormElement, extra: Params = {}): Promise<void> {
  const meta = formMeta(form);
  // Квиз считает себя сам: смешивать его заявки с обычными нельзя,
  // иначе одна отправка даст Директу две конверсии.
  const goal: Goal = meta.form_id === "quiz_form" ? "quiz_lead_success" : "lead_form_success";
  return trackAndWait(goal, { ...meta, service: serviceOf(), ...extra });
}

/** Ошибка доставки. Микрособытие: целью в Мастере кампаний быть не должно. */
export function leadError(form: HTMLFormElement, errorType: string): void {
  trackMetrika("lead_form_error", { form_id: formMeta(form).form_id, error_type: errorType });
}

export function validationError(form: HTMLFormElement, field: string, errorType: string): void {
  trackMetrika("form_validation_error", { form_id: formMeta(form).form_id, field, error_type: errorType });
}

/** Тип ошибки по невалидному полю. Значение поля никуда не передаётся — только имя. */
function errorTypeOf(field: HTMLInputElement | HTMLSelectElement): string {
  if (field.type === "checkbox") return "privacy_not_checked";
  if ((field as HTMLInputElement).validity?.valueMissing) return "empty_required_field";
  if (field.name === "contact") return "invalid_phone";
  if (field.type === "email") return "invalid_email";
  return "invalid_value";
}

/**
 * `form_start` и `form_view` — по одному разу на форму за визит.
 * Ключ визита держим в sessionStorage: перезагрузка страницы не должна
 * превращать одного человека в десять «начавших заполнять».
 */
const seen = new Set<string>();

function onceThisVisit(key: string): boolean {
  if (seen.has(key)) return false;
  seen.add(key);
  try {
    const store = `an:${key}`;
    if (sessionStorage.getItem(store)) return false;
    sessionStorage.setItem(store, "1");
  } catch { /* приватный режим: ограничимся защитой в памяти */ }
  return true;
}

/**
 * Подписывает форму на события заполнения. Вызывается и на форму из разметки,
 * и на форму квиза, которой в разметке нет — она появляется после расчёта.
 */
const formKeys = new WeakMap<HTMLFormElement, string>();
const keyCount = new Map<string, number>();

/**
 * Ключ «одна форма — одно событие за визит». Отдельно от `form_id`, потому что
 * словарь идентификаторов из ТЗ короткий: попап «в 1 клик» и exit-попап оба
 * `modal_form`, и по общему ключу второй попап молчал бы в отчётах.
 */
function keyOf(form: HTMLFormElement): string {
  const known = formKeys.get(form);
  if (known) return known;
  const meta = formMeta(form);
  const base = `${meta.form_id}:${meta.placement}:${meta.lead_type}`;
  const seenCount = (keyCount.get(base) ?? 0) + 1;
  keyCount.set(base, seenCount);
  const key = seenCount === 1 ? base : `${base}#${seenCount}`;
  formKeys.set(form, key);
  return key;
}

export function watchForm(form: HTMLFormElement): void {
  const meta = formMeta(form);
  const key = keyOf(form);

  // Первый ввод. Содержимое поля не передаём — только его имя, так требует ТЗ
  // и здравый смысл: персональные данные в Метрике не нужны никому.
  const onFirstInput = (event: Event) => {
    const field = event.target as HTMLInputElement;
    if (!onceThisVisit(`start:${key}`)) return;
    trackMetrika("form_start", { ...meta, first_field: field.name || "unknown" });
  };
  form.addEventListener("input", onFirstInput);
  form.addEventListener("change", onFirstInput);

  // Браузерная валидация срабатывает раньше нашего submit и просто не даёт
  // форме отправиться. Без этого слушателя незаполненное согласие выглядело бы
  // в отчётах как «человек посмотрел форму и передумал».
  form.addEventListener("invalid", (event) => {
    const field = event.target as HTMLInputElement;
    validationError(form, field.name || "unknown", errorTypeOf(field));
  }, true);

  if (!("IntersectionObserver" in window)) return;
  const observer = new IntersectionObserver((entries) => {
    if (!entries.some((entry) => entry.isIntersecting)) return;
    observer.disconnect();
    if (onceThisVisit(`view:${key}`)) trackMetrika("form_view", meta);
  }, { threshold: 0.3 });
  observer.observe(form);
}

// --- Единая подписка на страницу ----------------------------------------

let started = false;

export function startAnalytics(): void {
  if (started) return;
  started = true;

  fillTrackingFields();

  document.addEventListener("click", (event) => {
    const target = event.target as HTMLElement | null;
    if (!target) return;

    const cta = target.closest<HTMLElement>('[data-analytics="cta"]');
    if (cta) {
      trackMetrika("cta_click", {
        cta_id: cta.dataset.ctaId || "unknown_cta",
        placement: cta.dataset.placement || "unknown",
        target: cta.dataset.target || "unknown",
      });
    }

    // Клик по телефону ничего не отменяет и не задерживает: на мобильном
    // между кликом и открытием звонилки нельзя вставлять ожидание.
    const phone = target.closest<HTMLElement>('[data-analytics="phone"]');
    if (phone) {
      trackMetrika("phone_click", {
        placement: phone.dataset.placement || "unknown",
        phone_type: phone.dataset.phoneType || "main",
      });
    }

    const messenger = target.closest<HTMLElement>('[data-analytics="messenger"]');
    if (messenger) {
      trackMetrika("messenger_click", {
        channel: messenger.dataset.channel || "unknown",
        placement: messenger.dataset.placement || "unknown",
      });
    }

    const download = target.closest<HTMLElement>('[data-analytics="download"]');
    if (download) {
      trackMetrika("download_click", {
        file_name: download.dataset.fileName || "unknown",
        file_type: download.dataset.fileType || "pdf",
      });
    }
  });

  document.querySelectorAll<HTMLFormElement>("[data-analytics-form]").forEach(watchForm);

  // FAQ. Один вопрос — одно событие за визит: иначе человек, который свернул
  // и развернул ответ трижды, выглядит как три заинтересованных посетителя.
  document.querySelectorAll<HTMLDetailsElement>("[data-faq-id]").forEach((item) => {
    item.addEventListener("toggle", () => {
      if (!item.open) return;
      const id = item.dataset.faqId!;
      if (!onceThisVisit(`faq:${id}`)) return;
      trackMetrika("faq_open", { faq_id: id, question_number: Number(item.dataset.questionNumber ?? 0) });
    });
  });

  watchScroll();
}

/** Глубина просмотра — один раз на страницу за визит. */
function watchScroll(): void {
  const key = `scroll75:${location.pathname}`;
  const check = () => {
    const height = document.documentElement.scrollHeight - window.innerHeight;
    if (height <= 0) return;                       // страница короче экрана — считать нечего
    const share = (window.scrollY) / height;
    if (share < 0.75) return;
    window.removeEventListener("scroll", check);
    if (onceThisVisit(key)) trackMetrika("scroll_75", {});
  };
  window.addEventListener("scroll", check, { passive: true });
  check();
}
