/**
 * Обработчик всех форм заявки на странице: обычной, exit-попапа и «в 1 клик».
 *
 * Раньше жил внутри LeadForm.astro, и попапы работали только потому, что
 * LeadForm оказывалась на той же странице. На каталоге её не будет — вынесли
 * в модуль, который каждый компонент с формой импортирует сам.
 *
 * Способ связи форма задаёт одним из трёх способов:
 *   — кнопки `.channel__btn` — большая форма замера;
 *   — `select[name=channel]` — прайс-лист, там это «куда отправить»;
 *   — `data-channel` на самой форме — «в 1 клик», там только телефон.
 */

import { leadError, leadSuccess, trackingParams, validationError, watchForm } from "./analytics";

const HINTS: Record<string, [подпись: string, подсказка: string]> = {
  telegram: ["Введите телефон или @username в Telegram", "+7 или @username"],
  max: ["Введите номер телефона в MAX", "+7 "],
  phone: ["Введите ваш телефон", "+7 "],
};

/**
 * Проверка контакта на клиенте. Сервер проверяет то же самое и остаётся
 * последним словом — здесь это нужно, чтобы человек увидел ошибку сразу
 * и чтобы в Метрику ушло `form_validation_error` с типом, а не молчание.
 */
function contactProblem(value: string, channel: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return "empty_required_field";
  if (channel === "telegram" && /^@[\w\d_]{4,32}$/.test(trimmed)) return null;
  const digits = trimmed.replace(/\D/g, "");
  return digits.length < 10 || digits.length > 11 ? "invalid_phone" : null;
}

const PROBLEM_TEXT: Record<string, string> = {
  empty_required_field: "Оставьте номер телефона",
  invalid_phone: "Проверьте номер телефона",
};

function bind(form: HTMLFormElement) {
  const startedAt = Date.now();
  watchForm(form);
  const contact = form.querySelector<HTMLInputElement>("[data-contact]")!;
  const label = form.querySelector<HTMLElement>("[data-contact-label]");
  const buttons = form.querySelectorAll<HTMLButtonElement>(".channel__btn");
  const picker = form.querySelector<HTMLSelectElement>("select[name=channel]");

  // Подпись поля выводится из активной кнопки, а не задаётся отдельно.
  // Иначе они расходятся: выбран Telegram, а форма просит телефон —
  // ровно это и было видно на первой загрузке страницы.
  let channel = "telegram";
  const select = (value: string) => {
    channel = value;
    buttons.forEach((b) => b.classList.toggle("is-active", b.dataset.channel === value));
    if (label) [label.textContent, contact.placeholder] = HINTS[value];
  };

  buttons.forEach((button) => {
    button.addEventListener("click", () => select(button.dataset.channel!));
  });

  picker?.addEventListener("change", () => select(picker.value));

  select(
    picker?.value
      ?? form.dataset.channel
      ?? form.querySelector<HTMLButtonElement>(".channel__btn.is-active")?.dataset.channel
      ?? "telegram",
  );

  // Мягкое форматирование: ничего не блокируем, шаблон в поле не подставляем
  contact.addEventListener("input", () => {
    if (contact.value.startsWith("@")) return;
    const digits = contact.value.replace(/\D/g, "").replace(/^8/, "7").slice(0, 11);
    if (!digits) return;
    const rest = digits.slice(1);
    contact.value = "+7"
      + (rest.length ? ` (${rest.slice(0, 3)}` : "")
      + (rest.length > 3 ? `) ${rest.slice(3, 6)}` : "")
      + (rest.length > 6 ? `-${rest.slice(6, 8)}` : "")
      + (rest.length > 8 ? `-${rest.slice(8, 10)}` : "");
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("button[type=submit]") as HTMLButtonElement;
    const error = form.querySelector<HTMLElement>("[data-error]")!;

    // Формат номера браузер не проверяет: для него `+7 (12` — обычный текст.
    // Ловим до отправки, иначе человек получает отказ от сервера на ровном месте.
    const problem = contactProblem(contact.value, channel);
    if (problem) {
      validationError(form, "contact", problem);
      error.textContent = PROBLEM_TEXT[problem];
      error.hidden = false;
      contact.focus();
      return;
    }

    button.disabled = true;
    error.hidden = true;

    const field = (name: string) =>
      (form.elements.namedItem(name) as HTMLInputElement | null)?.value ?? "";

    const payload = {
      name: field("name"),                       // в форме «в 1 клик» имени нет
      contact: contact.value,
      channel,
      company: field("company"),
      consent: (form.elements.namedItem("consent") as HTMLInputElement).checked,
      elapsed: Date.now() - startedAt,
      page: location.pathname,
      geo: field("geo"),
      source: field("source") || null,           // товар или кейс, из которого пришли
      magnet: form.dataset.magnet || null,
      // Метки визита плюс сохранённые ранее: заявку могли оставить и через неделю
      // после клика по объявлению. Остальные параметры адреса тоже забираем —
      // по ним видно, с какой выборки каталога пришёл человек.
      utm: { ...Object.fromEntries(new URLSearchParams(location.search)), ...trackingParams() },
    };

    try {
      const response = await fetch("/api/lead", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      const json = await response.json();
      if (!response.ok) throw new Error(json.error ?? "Не удалось отправить");
      sessionStorage.setItem("lead", JSON.stringify({ name: payload.name, channel, download: json.download }));
      // Цель — только здесь, после ответа сервера. Ждём отправку события:
      // переход на «Спасибо» иначе обрывает запрос к Метрике на полуслове.
      await leadSuccess(form, { channel });
      location.href = "/thanks";
    } catch (problem) {
      const failed = problem instanceof TypeError;   // fetch не дошёл до сервера
      leadError(form, failed ? "network_error" : "server_error");
      error.textContent = problem instanceof Error ? problem.message : "Не удалось отправить";
      error.hidden = false;
      button.disabled = false;
    }
  });
}

document.querySelectorAll<HTMLFormElement>("[data-lead]").forEach(bind);
