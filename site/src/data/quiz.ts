/**
 * Конфигурация квизов-калькуляторов: по одному объекту на посадочную.
 *
 * Движок (`components/Quiz.astro`) одинаковый для обеих страниц: шаги, прогресс,
 * счётчик, экран результата и форма. Разное — вопросы, формула площади и прайс
 * на работы, и оно живёт здесь. Второй копией компонента это делать нельзя:
 * правка в форме или в антиботе тут же разъедется между двумя файлами.
 *
 * Строка данных счётчика (`/data/quiz-<направление>.json`, собирает
 * `pipeline/build_showcase.py`): цена всегда последняя, признак «цена за метр» —
 * перед ней, остальное своё для направления.
 */

export type Answers = Record<string, string | string[]>;
export type QuizRow = (string | number)[];

export type QuizOption = { value: string; label: string; note?: string };

export type QuizStep = {
  id: string;
  title: string;
  hint?: string;
  multiple?: boolean;
  options: QuizOption[];
};

/** Сколько метров считаем и как это объяснить человеку. */
export type AreaInfo = {
  /** Площадь, которую человек назвал — её показываем в шапке результата */
  area: number;
  /** Метры материала: облицовка плюс запас на подрезку */
  material: number;
  /** Строка-разбор: из чего сложились метры */
  note: string;
};

export type QuizConfig = {
  dataUrl: string;
  steps: QuizStep[];
  gifts: QuizOption[];
  /** Значения по умолчанию для подарка и канала связи */
  defaults: { gift: string; channel: string };
  /** Подходит ли позиция каталога под уже данные ответы */
  matches(row: QuizRow, answers: Answers): boolean;
  /** Метры по ответам */
  area(answers: Answers): AreaInfo;
  /** Вилка стоимости работ на весь объём, в рублях */
  work(area: AreaInfo, answers: Answers): [number, number];
  /** Выбранный подарок обнуляет работы? */
  workIsGift(answers: Answers): boolean;
  /** Склонения для счётчика: «подходит 412 покрытий» */
  counterForms: [string, string, string];
  /** Подпись строки материала в разбивке результата */
  materialLabel: string;
};

const price = (row: QuizRow) => row[row.length - 1] as number;
const perMetre = (row: QuizRow) => Boolean(row[row.length - 2]);

/** Метры в тексте — по-русски: «3,5», а не «3.5». */
const m = (value: number) => value.toLocaleString("ru-RU", { maximumFractionDigits: 1 });

/** Метры материала округляем вверх: половину плитки никто не продаёт, а запас
    на подрезку, округлённый вниз, — уже не запас. 6 м² + 7 % даёт 7, а не «те же 6». */
const withCut = (area: number) => Math.ceil(area * 1.07);

// ---------------------------------------------------------------- полы -----

// Площадь по числу комнат — типовые значения для предварительного расчёта.
const ROOM_AREA: Record<string, number> = { "1": 18, "2": 32, "3": 45, all: 60, unknown: 35 };

// Работы. ПЛЕЙСХОЛДЕР до получения прайса от клиента (см. 02_QUESTIONS.md, п. 8).
const POLY_WORK = {
  laying: [400, 600],
  underlay: [120, 180],
  skirting: [300, 400],
  thresholds: [400, 600],
  demolition: [150, 250],
  leveling: [400, 700],
} as const;

const poly: QuizConfig = {
  dataUrl: "/data/quiz-poly.json",
  counterForms: ["покрытие", "покрытия", "покрытий"],
  materialLabel: "Материал с запасом на подрезку",
  defaults: { gift: "laying", channel: "telegram" },

  steps: [
    {
      id: "kind",
      title: "Мне нужен пол…",
      options: [
        { value: "laminate", label: "Ламинат", note: "теплее на ощупь, дешевле" },
        { value: "spc", label: "Кварцвинил, SPC", note: "не боится воды" },
        { value: "any", label: "Не знаю — подберите", note: "решим на замере" },
      ],
    },
    {
      id: "rooms",
      title: "Пол нужен для…",
      options: [
        { value: "1", label: "Одной комнаты", note: "около 18 м²" },
        { value: "2", label: "Двух комнат", note: "около 32 м²" },
        { value: "3", label: "Трёх комнат", note: "около 45 м²" },
        { value: "all", label: "Всей квартиры", note: "от 60 м²" },
        { value: "unknown", label: "Пока не знаю", note: "посчитаем на замере" },
      ],
    },
    {
      id: "place",
      title: "Пол будет лежать…",
      options: [
        { value: "flat", label: "В квартире" },
        { value: "house", label: "В частном доме" },
        { value: "rent", label: "В квартире под аренду", note: "нужна износостойкость" },
        { value: "commercial", label: "В коммерческом помещении", note: "43 класс и выше" },
      ],
    },
    {
      id: "warm",
      title: "Тёплый пол…",
      options: [
        { value: "yes", label: "Уже есть" },
        { value: "plan", label: "Планирую" },
        { value: "no", label: "Не будет" },
      ],
    },
    {
      id: "extra",
      title: "Кроме покрытия нужно…",
      hint: "можно выбрать несколько",
      multiple: true,
      options: [
        { value: "underlay", label: "Подложка" },
        { value: "skirting", label: "Плинтус" },
        { value: "thresholds", label: "Порожки" },
        { value: "demolition", label: "Снять старый пол" },
        { value: "leveling", label: "Выровнять основание" },
        { value: "none", label: "Только покрытие" },
      ],
    },
  ],

  gifts: [
    { value: "laying", label: "Укладка в подарок", note: "при заказе от 30 м²" },
    { value: "skirting", label: "Плинтус и порожки", note: "к покрытию" },
    { value: "storage", label: "Хранение 3 месяца", note: "на нашем складе" },
  ],

  matches(row, answers) {
    const [kind, klass, warm] = row as [string, number, number];
    if (answers.kind && answers.kind !== "any" && kind !== answers.kind) return false;
    if ((answers.warm === "yes" || answers.warm === "plan") && !warm) return false;
    if (answers.place === "rent" || answers.place === "commercial") {
      const enough = kind === "spc" ? klass >= 42 : klass >= 33;
      if (!enough) return false;
    }
    return true;
  },

  area(answers) {
    const area = ROOM_AREA[String(answers.rooms ?? "unknown")] ?? 35;
    const material = withCut(area);
    return { area, material, note: `${m(area)} м² пола плюс 7 % на подрезку — ${m(material)} м² покрытия` };
  },

  work({ area }, answers) {
    const extras = Array.isArray(answers.extra) ? answers.extra : [];
    const perimeter = Math.round(4 * Math.sqrt(area));
    let low = POLY_WORK.laying[0] * area;
    let high = POLY_WORK.laying[1] * area;
    if (extras.includes("underlay")) { low += POLY_WORK.underlay[0] * area; high += POLY_WORK.underlay[1] * area; }
    if (extras.includes("skirting")) { low += POLY_WORK.skirting[0] * perimeter; high += POLY_WORK.skirting[1] * perimeter; }
    if (extras.includes("thresholds")) { low += POLY_WORK.thresholds[0] * 2; high += POLY_WORK.thresholds[1] * 4; }
    if (extras.includes("demolition")) { low += POLY_WORK.demolition[0] * area; high += POLY_WORK.demolition[1] * area; }
    if (extras.includes("leveling")) { low += POLY_WORK.leveling[0] * area; high += POLY_WORK.leveling[1] * area; }
    return [Math.round(low), Math.round(high)];
  },

  workIsGift: (answers) => answers.gift === "laying",
};

// -------------------------------------------------------------- плитка -----

// Середины названных диапазонов. Для санузла это площадь ПОМЕЩЕНИЯ, а не облицовки:
// человек знает свои «четыре квадрата», а сколько это плитки — как раз наша работа.
const TILE_AREA: Record<string, number> = { "4": 3.5, "8": 6, "15": 11, big: 20, unknown: 6 };

const CEILING = 2.5;      // высота стен, типовая для панельного дома
const DOOR = 1.6;         // дверной проём, который облицовывать не нужно

// Работы. ПЛЕЙСХОЛДЕР, кроме крупноформата: 1 500 ₽/м² — единственная цифра,
// названная в брифе (02_QUESTIONS.md, п. 8 и 22). Мелкий формат дороже в укладке:
// швов втрое больше, и раскладка «кабанчиком» требует разметки.
const TILE_WORK = {
  laying: {
    large: [1500, 1500],
    "600": [1200, 1600],
    small: [1800, 2400],
    other: [1200, 1800],
    any: [1200, 1800],
  } as Record<string, readonly [number, number]>,
  demolition: [400, 700],
  waterproof: [300, 500],
} as const;

const plitka: QuizConfig = {
  dataUrl: "/data/quiz-plitka.json",
  counterForms: ["коллекция", "коллекции", "коллекций"],
  materialLabel: "Плитка с запасом на подрезку",
  defaults: { gift: "design", channel: "telegram" },

  steps: [
    {
      id: "room",
      title: "Отделываем…",
      options: [
        { value: "bath", label: "Санузел", note: "пол и стены" },
        { value: "apron", label: "Кухонный фартук" },
        { value: "floor", label: "Пол в квартире" },
        { value: "porch", label: "Крыльцо, балкон, фасад", note: "нужен морозостойкий" },
        { value: "other", label: "Другое", note: "уточним на замере" },
      ],
    },
    {
      id: "size",
      title: "Площадь примерно…",
      hint: "для санузла — площадь помещения, стены посчитаем сами",
      options: [
        { value: "4", label: "До 4 м²", note: "типовой санузел" },
        { value: "8", label: "4–8 м²" },
        { value: "15", label: "8–15 м²" },
        { value: "big", label: "Больше 15 м²" },
        { value: "unknown", label: "Не знаю — посчитайте сами", note: "замерим бесплатно" },
      ],
    },
    {
      id: "format",
      title: "Из форматов нравится…",
      options: [
        { value: "small", label: "Мелкий формат и «кабанчик»", note: "20×20, 10×30" },
        { value: "600", label: "600×600", note: "универсальный" },
        { value: "large", label: "Крупный, 600×1200 и больше", note: "меньше швов" },
        { value: "any", label: "Не определился — подберите", note: "покажем варианты" },
      ],
    },
    {
      id: "scope",
      title: "Нужен…",
      options: [
        { value: "material", label: "Только материал", note: "расчёт количества бесплатно" },
        { value: "laying", label: "Материал и укладка" },
        { value: "turnkey", label: "Под ключ с демонтажом", note: "старую плитку снимем сами" },
      ],
    },
    {
      id: "when",
      title: "Начинаем…",
      options: [
        { value: "now", label: "Сейчас" },
        { value: "soon", label: "Через 1–3 месяца" },
        { value: "look", label: "Пока присматриваюсь", note: "цену закрепим на 14 дней" },
      ],
    },
  ],

  gifts: [
    { value: "design", label: "Дизайн-проект санузла", note: "от 8 000 ₽, за 7 дней" },
    { value: "storage", label: "Хранение материала 3 месяца", note: "на нашем складе" },
    { value: "delivery", label: "Доставка по городу", note: "к началу работ" },
  ],

  matches(row, answers) {
    const [, sub, frost] = row as [string, string, number];
    // Улица — единственное свойство плитки, заполненное в выгрузке настолько,
    // чтобы на него можно было опираться: морозостойкость есть у 1 904 позиций.
    if (answers.room === "porch" && !frost) return false;
    if (answers.format && answers.format !== "any" && sub !== answers.format) return false;
    return true;
  },

  area(answers) {
    const area = TILE_AREA[String(answers.size ?? "unknown")] ?? 6;

    // Санузел — единственный случай, где названная площадь и площадь облицовки
    // расходятся втрое. Человек говорит «четыре квадрата», имея в виду пол,
    // а плитку нужно считать вместе со стенами. Показать этот пересчёт полезнее,
    // чем спрятать: ровно в этом месте люди и ошибаются с количеством.
    if (answers.room === "bath") {
      const perimeter = 4 * Math.sqrt(area);
      const walls = Math.max(Math.round(perimeter * CEILING - DOOR), 0);
      const material = withCut(area + walls);
      return {
        area,
        material,
        note: `пол ${m(area)} м² плюс стены ${m(walls)} м², с запасом на подрезку — ${m(material)} м² плитки`,
      };
    }

    const material = withCut(area);
    return { area, material, note: `${m(area)} м² облицовки плюс 7 % на подрезку — ${m(material)} м² плитки` };
  },

  work(info, answers) {
    if (answers.scope === "material") return [0, 0];

    const format = String(answers.format ?? "any");
    const rate = TILE_WORK.laying[format] ?? TILE_WORK.laying.any;
    // Работы считаем по облицовке без подрезки: кладут метры стены и пола,
    // а не купленные метры плитки.
    const covered = answers.room === "bath" ? info.material / 1.07 : info.area;

    let low = rate[0] * covered;
    let high = rate[1] * covered;

    if (answers.scope === "turnkey") {
      low += TILE_WORK.demolition[0] * covered;
      high += TILE_WORK.demolition[1] * covered;
    }
    if (answers.room === "bath") {
      low += TILE_WORK.waterproof[0] * info.area;
      high += TILE_WORK.waterproof[1] * info.area;
    }
    return [Math.round(low), Math.round(high)];
  },

  // Подарок здесь — дизайн-проект, а не работы: обнулять укладку нечем.
  workIsGift: () => false,
};

export const QUIZ: Record<string, QuizConfig> = { poly, plitka };
export { price as rowPrice, perMetre as rowPerMetre };
