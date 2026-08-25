#!/usr/bin/env python3
"""Шаг 3: catalog.json → showcase.json — витрина для лендингов.

Запуск:  python3 pipeline/build_showcase.py

На посадочной не нужен весь каталог: нужны 3–4 подборки по 6–8 товаров, которые
быстро грузятся и наглядно показывают ассортимент. Берём только «показательные»:
с фото, описанием, заполненными свойствами и ценой из середины диапазона —
дешёвый хвост и случайные дорогие позиции портят и вид, и ожидания по цене.
"""
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

# Лежит рядом: при запуске «python3 pipeline/build_showcase.py»
# каталог скрипта и так первый в sys.path.
from filters import drop_piece_decor, is_product

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "catalog.json"
# Пишем внутрь site/: контекст сборки контейнера — папка site, снаружи файлы не видны
OUT = ROOT / "site" / "src" / "data" / "showcase.json"
# Данные счётчика грузятся лениво при открытии квиза, поэтому лежат в public/
QUIZ_DIR = ROOT / "site" / "public" / "data"
# Сюда fetch_images.py складывает скачанные и пережатые фото.
PHOTOS = ROOT / "site" / "public" / "showcase"

PER_COLLECTION = 2   # не больше двух товаров одной коллекции в подборке
SIZE = 4             # товаров в подборке


def local_photo(item_id):
    """Путь к нашему WebP, если он уже скачан, иначе None.

    Раньше витрина всегда писала ссылку на фото сайта магазина, а подменял её
    на локальную только fetch_images.py — следующим шагом. Стоило перегенерировать
    витрину после него, и лендинг молча возвращался к чужим тяжёлым JPEG:
    страница начинала зависеть от того, жив ли сейчас сайт магазина.
    Правило именования файла повторяет safe_name() из fetch_images.py.
    """
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", item_id).strip("_")[:80] + ".webp"
    return f"/showcase/{name}" if (PHOTOS / name).exists() else None

# Подборки. Условие — функция от товара; порядок задаёт порядок блоков на странице.
SELECTIONS = {
    "poly": [
        {
            "slug": "warm-floor",
            "title": "Можно класть на тёплый пол",
            "note": "Проверено по паспорту покрытия",
            "match": lambda i: i["params"].get("Тёплый пол", "").startswith("Да"),
        },
        {
            "slug": "laminate-33",
            "title": "Ламинат 33 класса для квартиры",
            "note": "Рабочий выбор для кухни, коридора и гостиной",
            "match": lambda i: i["group"] == "Ламинат" and i["subgroup"] == "33 класс",
        },
        {
            "slug": "spc-waterproof",
            "title": "Кварцвинил, который не боится воды",
            "note": "SPC: не разбухает и не расходится в замках",
            "match": lambda i: i["group"] == "Кварцвинил и ПВХ"
            and i["params"].get("Влагостойкий", "").startswith("Да"),
        },
        {
            "slug": "class-43",
            "title": "43 класс — под аренду и коммерцию",
            "note": "Повышенная износостойкость",
            "match": lambda i: i["subgroup"] == "43 класс",
        },
    ],
    "plitka": [
        {
            "slug": "large-format",
            "title": "Крупноформат 600×1200 и больше",
            "note": "Меньше швов — проще уход",
            "match": lambda i: i["subgroup"] == "Крупноформат 600×1200 и больше",
        },
        {
            "slug": "small-format",
            "title": "Мелкий формат и «кабанчик»",
            "note": "Для акцентных стен и санузлов",
            "match": lambda i: i["subgroup"] == "Мелкий формат",
        },
        {
            "slug": "600x600",
            "title": "Керамогранит 600×600",
            "note": "Универсальный формат для пола",
            "match": lambda i: i["subgroup"] == "600×600",
        },
        {
            # Четвёртый блок нужен арифметически: витрина берёт по два товара
            # из подборки и ждёт восемь, а подборок у плитки было три.
            # Морозостойкость — единственное свойство плитки в выгрузке, которое
            # отвечает на реальный вопрос покупателя («а на крыльцо можно?»),
            # и заполнено оно у 1 904 позиций.
            "slug": "frost",
            "title": "Морозостойкий — крыльцо, балкон, фасад",
            "note": "Выдерживает улицу и перепады температур",
            "match": lambda i: i["params"].get("Морозостойкий", "").startswith("Да"),
        },
    ],
}


def score(item, low, high):
    """Насколько товар «показателен»: фото, описание, свойства, цена из середины."""
    points = 0
    # Без единицы измерения карточка показывает голое «5 556 ₽» рядом с «2 590 ₽/м²»,
    # и человек не понимает, за что цена. В каталоге такие позиции остаются
    # (их 633, и врать про «м²» мы не будем), но витрина — лицо страницы.
    points += 4 if item["unit"] else -8
    points += min(len(item["pictures"]), 3) * 2      # несколько ракурсов — плюс
    points += 3 if len(item["description"]) > 120 else 0
    points += min(len(item["params"]), 8)
    points += 2 if item["vendor"] else 0
    points += 3 if low <= item["price"] <= high else 0
    points += 1 if item["available"] else 0
    if re.search(r"\b(2 сорт|уценк|образец)\b", item["name"], re.I):
        points -= 10
    return points


def pick(items, selection, taken=()):
    """taken — id, уже занятые предыдущими подборками.

    Без этого блоки пересекаются: морозостойкий керамогранит 600×600 подходит
    сразу двум условиям, и один товар вставал в витрину дважды.
    """
    pool = [i for i in items if selection["match"](i) and i["pictures"] and i["id"] not in taken]
    if not pool:
        return []
    prices = sorted(i["price"] for i in pool)
    low = prices[len(prices) // 4]
    high = prices[3 * len(prices) // 4]

    used = defaultdict(int)
    chosen = []
    for item in sorted(pool, key=lambda i: -score(i, low, high)):
        key = item["params"].get("Коллекция") or item["vendor"] or item["id"]
        if used[key] >= PER_COLLECTION:
            continue
        used[key] += 1
        chosen.append(item)
        if len(chosen) == SIZE:
            break
    return chosen


# Подгруппы плитки → короткие коды для счётчика. Строкой «Крупноформат 600×1200
# и больше» в JSON на 2 665 позиций набегает лишних 60 КБ на пустом месте.
PLITKA_SUBS = {
    "Крупноформат 600×1200 и больше": "large",
    "600×600": "600",
    "Мелкий формат": "small",
    "Другие форматы": "other",
}


def quiz_row(item):
    """Одна строка среза — своя для каждого направления.

    Полы: [тип, класс, тёплый пол, влагостойкий, за_метр, цена].
    Плитка: [материал, формат, морозостойкий, за_метр, цена] — классов
    и влагостойкости в выгрузке у плитки нет (заполнены у двух товаров из 2 665),
    зато есть морозостойкость: по ней и отвечаем на «а на крыльцо можно?».

    Цена всегда последняя, признак «за_метр» — перед ней. Признак нужен, потому что
    у 633 позиций плитки единица измерения в выгрузке пустая, и непонятно, метр это
    или упаковка. Счётчик такие позиции считает (они реально есть в каталоге),
    а в вилку расчёта они не идут: диапазон «от и до» человек читает как рубли
    за метр, и одна цена за упаковку задирает верх на ровном месте.
    """
    params = item["params"]
    if item["direction"] == "poly":
        if item["group"] == "Ламинат":
            kind = "laminate"
        elif item["group"] == "Кварцвинил и ПВХ":
            kind = "spc"
        else:
            kind = "other"
        klass = re.sub(r"\D", "", params.get("Класс", "")) or "0"
        return [
            kind,
            int(klass),
            1 if params.get("Тёплый пол", "").startswith("Да") else 0,
            1 if params.get("Влагостойкий", "").startswith("Да") else 0,
            1 if item["unit"] == "м²" else 0,
            item["price"],
        ]

    return [
        "gres" if item["group"] == "Керамогранит" else "tile",
        PLITKA_SUBS.get(item["subgroup"], "other"),
        1 if params.get("Морозостойкий", "").startswith("Да") else 0,
        1 if item["unit"] == "м²" else 0,
        item["price"],
    ]


def build_quiz_data(items):
    """Компактный срез для живого счётчика в квизе — по файлу на направление.

    Счётчик показывает, сколько позиций осталось после каждого ответа, поэтому число
    обязано считаться по той же выборке, что уходит в фид и в каталог — иначе
    получится маркетинговая цифра, а мы обещаем честную. Отсюда же и is_product:
    клин за 78 ₽ в каталоге не показан, значит и в 25-м процентиле квиза
    ему делать нечего — иначе расчёт начнётся со слов «плитка от 78 ₽/м²».
    """
    QUIZ_DIR.mkdir(parents=True, exist_ok=True)
    for direction in SELECTIONS:
        pool = drop_piece_decor([i for i in items
                                 if i["direction"] == direction and is_product(i)])
        rows = [quiz_row(i) for i in pool]
        prices = sorted(r[-1] for r in rows if r[-2])
        path = QUIZ_DIR / f"quiz-{direction}.json"
        path.write_text(json.dumps({
            "items": rows,
            "price": {
                "p25": prices[len(prices) // 4],
                "median": prices[len(prices) // 2],
                "p75": prices[3 * len(prices) // 4],
            },
        }, ensure_ascii=False), encoding="utf-8")
        print(f"данные квиза «{direction}»: {len(rows)} позиций "
              f"({len(prices)} с ценой за м²), "
              f"{path.stat().st_size / 1024:.0f} КБ → {path.relative_to(ROOT)}")


def main():
    items = json.loads(CATALOG.read_text(encoding="utf-8"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    result = {}

    for direction, selections in SELECTIONS.items():
        pool = [i for i in items if i["direction"] == direction]
        blocks = []
        taken: set[str] = set()
        for selection in selections:
            chosen = pick(pool, selection, taken)
            taken.update(i["id"] for i in chosen)
            if not chosen:
                print(f"  ⚠ подборка «{selection['title']}» пуста — проверьте условие")
                continue
            prices = [i["price"] for i in chosen]
            blocks.append({
                "slug": selection["slug"],
                "title": selection["title"],
                "note": selection["note"],
                "price_from": min(prices),
                "items": [{
                    "id": i["id"],
                    "name": i["name"],
                    "url": i["url"],
                    "price": i["price"],
                    "unit": i["unit"],
                    "vendor": i["vendor"],
                    "picture": local_photo(i["id"]) or i["pictures"][0],
                    "params": {k: v for k, v in i["params"].items()
                               if k in ("Формат", "Класс", "Толщина, мм", "Коллекция", "Тёплый пол")},
                } for i in chosen],
            })
            print(f"  {selection['title']}: {len(chosen)} шт, "
                  f"от {min(prices)} до {max(prices)} ₽, медиана {int(statistics.median(prices))}")
        result[direction] = blocks

    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    build_quiz_data(items)
    total = sum(len(b["items"]) for blocks in result.values() for b in blocks)
    print(f"\nвитрина собрана: {total} товаров в "
          f"{sum(len(b) for b in result.values())} подборках → {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
