#!/usr/bin/env python3
"""Шаг 1: выгрузка AdvantShop (CSV) → нормализованный catalog.json.

Запуск:  python3 pipeline/build_catalog.py [путь_к_csv]

Что делает:
  • берёт только включённые товары целевых направлений (плитка/керамогранит, полы);
  • отбрасывает непригодное для рекламы: без цены, без фото, «2 СОРТ»;
  • нормализует грязь из выгрузки — единицы измерения, форматы, бренды;
  • собирает URL картинок из имён файлов;
  • накладывает ручные перебивки из pipeline/overrides.json, чтобы наши правки
    не затирались следующей выгрузкой.

Результат: data/catalog.json + отчёт в консоль. Без внешних зависимостей.
"""
import csv
import html
import json
import re
import sys
from pathlib import Path

csv.field_size_limit(10**9)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "docs" / "catalog.csv"
OUT_JSON = ROOT / "data" / "catalog.json"
OVERRIDES = ROOT / "pipeline" / "overrides.json"

SITE = "https://moiremont18.ru"
PICTURE_SIZE = "big"  # small | middle | big
MAX_ID = 100  # ограничение Яндекса на длину id оффера

# Корни категорий магазина → наши рекламные направления
DIRECTIONS = {
    "plitka": {"Плитка и керамогранит", "Керамогранит"},
    "poly": {"Напольные покрытия"},
}

# Материал плитки — по названию товара, а не по категории магазина. Категории у клиента
# набиты по формату, поэтому в «Керамограните» лежали 63 товара с названием
# «Керамическая плитка», а в «Керамической плитке» — 24 керамогранита. Для объявления
# «купить керамогранит» это прямое несовпадение: человек кликает по керамограниту,
# а сверху выборки стоит настенная плитка.
# «Грес» и «гранит керамический» — те же керамогранит, только словами поставщика.
GRES_NAME = re.compile(r"керамогранит|гранит\s+керамическ|плитка\s+грес|\bгрес\b", re.I)
TILE_NAME = re.compile(r"керамическая\s+плитка|плитка\s+керамическая|^\s*(?:настенная|напольная)?\s*плитка\b", re.I)

UNITS = {
    "м2": "м²", "квадратный метр": "м²", "м²": "м²",
    "шт": "шт", "шт.": "шт", "штука": "шт",
    "пара": "пара", "комплект": "компл",
}

# Свойства, которые переносим в фид как <param> (имя в CSV → имя в фиде)
PARAMS = {
    "Свойство: Формат": "Формат",
    "Свойство: Толщина, мм": "Толщина, мм",
    "Свойство: Класс": "Класс",
    "Свойство: Коллекция": "Коллекция",
    "Свойство: Поверхность": "Поверхность",
    "Свойство: Цвет": "Цвет",
    "Свойство: Страна": "Страна",
    "Свойство: Упаковка, м2": "В упаковке, м²",
    "Свойство: Совместимость с теплыми полами": "Тёплый пол",
    "Свойство: Влагостойкий": "Влагостойкий",
    "Свойство: Фаска": "Фаска",
    "Свойство: Морозостойкий": "Морозостойкий",
}


# Мусор в начале и в конце названий — только у плитки (у полов ноль таких).
# «GT60601503MR Плитка грес глаз. Anima_GT Белый 60*60 _ 1 \43,2» — это выгрузка
# из учётной системы: ведущий артикул и хвост с количеством в упаковке.
# Человек читает название на карточке и в объявлении, и такое там быть не должно.
LEAD_ARTICLE = re.compile(r"^[A-Z]{1,4}[0-9][A-Z0-9._-]{3,}\s+", re.I)
TAIL_NOISE = re.compile(r"\s*[_\\]+\s*[\d.,\s\\/]*$")


# Сокращения поставщика, которые человек на карточке не читает.
# «Грес» — это и есть керамогранит (gres porcellanato), не наша трактовка,
# а термин; «глаз.» — глазурованный. «_GT» — суффикс Global Tile в артикуле
# коллекции, к названию коллекции он отношения не имеет.
NAME_FIXES = (
    (re.compile(r"\bплитка\s+грес\s+глаз\.?", re.I), "Керамогранит глазурованный"),
    (re.compile(r"\bгрес\s+глаз\.?", re.I), "керамогранит глазурованный"),
    (re.compile(r"_GT\b"), ""),
    (re.compile(r"(?<=\d)\s*[*xх]\s*(?=\d)"), "×"),
)


def clean_name(value):
    """Убирает служебный мусор из названия, не трогая сам товар."""
    text = TAIL_NOISE.sub("", LEAD_ARTICLE.sub("", value))
    for pattern, replacement in NAME_FIXES:
        text = pattern.sub(replacement, text)
    return re.sub(r"\s{2,}", " ", text).strip(" -–—,;")


def clean(value):
    """Снимает HTML-разметку и лишние пробелы — в описаниях выгрузки встречается и то, и другое."""
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    # Описания собраны по шаблону, и незаполненные места оставляют дыры:
    # «порода дерева , С фаской», «толщиной  мм». Прибираем самое заметное.
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    text = re.sub(r"([,;:])\s*([,.;:])", r"\1", text)
    return text.strip(" ,;:")


def make_id(article, used):
    """Готовит id оффера: без пробелов и кавычек, не длиннее 100 символов, уникальный.

    Преобразование детерминированное — при следующей выгрузке тот же артикул даст
    тот же id, иначе Директ посчитает офферы новыми и статистика обнулится.
    """
    offer_id = re.sub(r"[\s\"'<>&]+", "_", article).strip("_")[:MAX_ID]
    if offer_id in used:
        suffix = 2
        while f"{offer_id[:MAX_ID - 3]}_{suffix}" in used:
            suffix += 1
        offer_id = f"{offer_id[:MAX_ID - 3]}_{suffix}"
    used.add(offer_id)
    return offer_id


def to_float(value):
    try:
        return float((value or "0").replace(" ", "").replace("\xa0", "").replace(",", "."))
    except ValueError:
        return 0.0


def normalize_unit(value):
    return UNITS.get((value or "").strip().lower(), (value or "").strip())


def normalize_format(value):
    """«600x1200», «600х1200х9», «1200х600» → «600×1200».

    В выгрузке формат пишут и латинской x, и кириллической х, с толщиной и без,
    а 600х1200 и 1200х600 — один и тот же формат. Приводим к одному виду,
    иначе подгруппы фида рассыплются на десятки почти одинаковых.
    """
    raw = (value or "").strip().lower().replace("x", "х").replace("*", "х")
    numbers = [n.replace(",", ".") for n in re.findall(r"\d+(?:[.,]\d+)?", raw)]
    if len(numbers) < 2:
        return ""
    side_a, side_b = sorted(float(n) for n in numbers[:2])
    fmt = lambda n: str(int(n)) if n == int(n) else str(n)  # noqa: E731
    return f"{fmt(side_a)}×{fmt(side_b)}"


def picture_urls(value):
    """«11936.png;11937.png» → полные URL картинок.

    В CSV лежат только имена файлов, реальный адрес собирается по схеме
    /pictures/product/{size}/{имя}_{size}.{ext} — проверено на витрине.
    """
    urls = []
    for name in re.split(r"[;\n]", value or ""):
        name = name.strip()
        if not name or "." not in name:
            continue
        stem, ext = name.rsplit(".", 1)
        urls.append(f"{SITE}/pictures/product/{PICTURE_SIZE}/{stem}_{PICTURE_SIZE}.{ext}")
    return urls


def category_paths(row):
    return [p for p in ((row.get(f"Категория: {i}") or "").strip() for i in range(1, 6)) if p]


def detect_direction(paths):
    for direction, roots in DIRECTIONS.items():
        if any(path.split(" >> ")[0] in roots for path in paths):
            return direction
    return None


def build_group(direction, paths, props, name=""):
    """Наша собственная группировка для дерева фида.

    Дерево магазина использовать нельзя: в нём два параллельных ветвления
    («Керамогранит» отдельным корнем и он же внутри «Плитки и керамогранита»),
    и один товар ушёл бы в Директ дважды. Поэтому группу считаем сами —
    из категории и свойств товара.

    У плитки группа — это материал (керамогранит или керамическая плитка), а формат
    уходит в подгруппу. Материал берём из названия: категории у клиента набиты
    по формату, и товар «Керамическая плитка Flat Negro 240х700» лежал в категории
    «Керамогранит». Если название материал не называет (бывает: «Centro», «Berlin»),
    остаёмся на категории магазина — она хотя бы не выдумана нами.
    """
    joined = " | ".join(paths)
    if direction == "plitka":
        if GRES_NAME.search(name):
            group = "Керамогранит"
        elif TILE_NAME.search(name):
            group = "Керамическая плитка"
        else:
            group = "Керамическая плитка" if "Мелко форматная" in joined else "Керамогранит"

        if "Мелко форматная" in joined:
            return (group, "Мелкий формат")
        fmt = normalize_format(props.get("Формат", ""))
        sides = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", fmt)] if fmt else []
        if sides and max(sides) >= 1200:
            return (group, "Крупноформат 600×1200 и больше")
        if fmt == "600×600":
            return (group, "600×600")
        return (group, "Другие форматы")

    if "Ламинат" in joined:
        klass = re.sub(r"\D", "", props.get("Класс", ""))
        return ("Ламинат", f"{klass} класс" if klass in {"31", "32", "33", "34"} else "Другие серии")
    if "ПВХ-полы" in joined or "кварц винил" in joined.lower():
        klass = re.sub(r"\D", "", props.get("Класс", ""))
        return ("Кварцвинил и ПВХ", f"{klass} класс" if klass in {"41", "42", "43", "44"} else "Другие серии")
    if "Кварцевый паркет" in joined:
        return ("Кварцевый паркет", "")
    if "Паркетная доска" in joined:
        return ("Паркетная доска", "")
    if "Подложка" in joined:
        return ("Подложка и комплектующие", "")
    return ("Другие напольные покрытия", "")


def main(csv_path=DEFAULT_CSV):
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8")) if OVERRIDES.exists() else {}
    items, seen, used_ids = [], set(), set()
    skipped = {"нет цены": 0, "нет фото": 0, "2 СОРТ": 0, "дубль артикула": 0, "не наше направление": 0}

    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            if (row.get("Включен") or "").strip() != "+":
                continue  # выключенные товары и строки-модификации
            paths = category_paths(row)
            direction = detect_direction(paths)
            if not direction:
                skipped["не наше направление"] += 1
                continue
            article = (row.get("Артикул") or "").strip()
            if not article or article in seen:
                skipped["дубль артикула"] += 1
                continue
            seen.add(article)

            if "2 СОРТ" in " | ".join(paths):
                skipped["2 СОРТ"] += 1
                continue
            price = to_float(row.get("Цена"))
            if price <= 0:
                skipped["нет цены"] += 1
                continue
            pictures = picture_urls(row.get("Фото товара"))
            if not pictures:
                skipped["нет фото"] += 1
                continue

            props = {name: clean(row.get(col)) for col, name in PARAMS.items() if clean(row.get(col))}
            # Формат приводим к одному виду прямо в данных, а не только при нарезке
            # подгрупп: иначе на карточке рядом стоят «1200х600х9» и «9 мм» —
            # толщина написана дважды, причём в первом случае слитно с форматом.
            if props.get("Формат"):
                props["Формат"] = normalize_format(props["Формат"]) or props["Формат"]
            title = clean_name(clean(row.get("Наименование")))
            group, subgroup = build_group(direction, paths, props, title)
            old_price = to_float(row.get("Числовая скидка в валюте товара"))

            item = {
                "id": make_id(article, used_ids),
                "article": article,
                "direction": direction,
                "group": group,
                "subgroup": subgroup,
                "name": title,
                "url": (row.get("Полный URL") or "").strip(),
                "price": round(price),
                "old_price": round(price + old_price) if old_price else None,
                "unit": normalize_unit(row.get("Ед. изм.")),
                "vendor": clean(row.get("Производитель")) or props.get("Бренд", ""),
                "pictures": pictures[:5],  # больше пяти Директ всё равно не использует
                "description": (clean(row.get("Описание")) or clean(row.get("Краткое описание")))[:2500],
                "available": to_float(row.get("Количество")) > 0,
                "params": props,
            }
            item.update(overrides.get(article, {}))
            items.append(item)

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"каталог собран: {len(items)} товаров → {OUT_JSON.relative_to(ROOT)}")
    if overrides:
        print(f"применено перебивок: {len(set(overrides) & seen)}")
    print("отброшено:", ", ".join(f"{k} — {v}" for k, v in skipped.items() if v))
    for direction in DIRECTIONS:
        chunk = [i for i in items if i["direction"] == direction]
        if not chunk:
            continue
        print(f"\n  направление «{direction}»: {len(chunk)}")
        groups = {}
        for item in chunk:
            key = f"{item['group']} / {item['subgroup']}".rstrip(" /")
            groups[key] = groups.get(key, 0) + 1
        for key, count in sorted(groups.items(), key=lambda kv: -kv[1]):
            print(f"    {count:5d}  {key}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV)
