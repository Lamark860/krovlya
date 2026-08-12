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

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "catalog.json"
# Пишем внутрь site/: контекст сборки контейнера — папка site, снаружи файлы не видны
OUT = ROOT / "site" / "src" / "data" / "showcase.json"
# Данные счётчика грузятся лениво при открытии квиза, поэтому лежат в public/
QUIZ_OUT = ROOT / "site" / "public" / "data" / "quiz-poly.json"

PER_COLLECTION = 2   # не больше двух товаров одной коллекции в подборке
SIZE = 4             # товаров в подборке

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
            "match": lambda i: i["subgroup"] == "Мелкоформатная плитка",
        },
        {
            "slug": "600x600",
            "title": "Керамогранит 600×600",
            "note": "Универсальный формат для пола",
            "match": lambda i: i["subgroup"] == "600×600",
        },
    ],
}


def score(item, low, high):
    """Насколько товар «показателен»: фото, описание, свойства, цена из середины."""
    points = 0
    points += min(len(item["pictures"]), 3) * 2      # несколько ракурсов — плюс
    points += 3 if len(item["description"]) > 120 else 0
    points += min(len(item["params"]), 8)
    points += 2 if item["vendor"] else 0
    points += 3 if low <= item["price"] <= high else 0
    points += 1 if item["available"] else 0
    if re.search(r"\b(2 сорт|уценк|образец)\b", item["name"], re.I):
        points -= 10
    return points


def pick(items, selection):
    pool = [i for i in items if selection["match"](i) and i["pictures"]]
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


def build_quiz_data(items):
    """Компактный срез для живого счётчика в квизе.

    Счётчик показывает, сколько позиций осталось после каждого ответа, поэтому число
    обязано считаться по той же выборке, что уходит в фид и в каталог — иначе
    получится маркетинговая цифра, а мы обещаем честную.
    Кортеж: [тип, класс, тёплый пол, влагостойкий, цена].
    """
    rows = []
    for item in items:
        if item["direction"] != "poly":
            continue
        if item["group"] == "Ламинат":
            kind = "laminate"
        elif item["group"] == "Кварцвинил и ПВХ":
            kind = "spc"
        else:
            kind = "other"
        klass = re.sub(r"\D", "", item["params"].get("Класс", "")) or "0"
        warm = 1 if item["params"].get("Тёплый пол", "").startswith("Да") else 0
        wet = 1 if item["params"].get("Влагостойкий", "").startswith("Да") else 0
        rows.append([kind, int(klass), warm, wet, item["price"]])

    prices = sorted(r[4] for r in rows)
    QUIZ_OUT.parent.mkdir(parents=True, exist_ok=True)
    QUIZ_OUT.write_text(json.dumps({
        "items": rows,
        "price": {
            "p25": prices[len(prices) // 4],
            "median": prices[len(prices) // 2],
            "p75": prices[3 * len(prices) // 4],
        },
    }, ensure_ascii=False), encoding="utf-8")
    print(f"данные квиза: {len(rows)} позиций → {QUIZ_OUT.relative_to(ROOT)}")


def main():
    items = json.loads(CATALOG.read_text(encoding="utf-8"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    result = {}

    for direction, selections in SELECTIONS.items():
        pool = [i for i in items if i["direction"] == direction]
        blocks = []
        for selection in selections:
            chosen = pick(pool, selection)
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
                    "picture": i["pictures"][0],
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
