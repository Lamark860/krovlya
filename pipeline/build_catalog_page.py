#!/usr/bin/env python3
"""Данные для страницы каталога: data/catalog.json → site/public/data/catalog-<направление>.json

Страница каталога рисуется на клиенте из этого файла, а не статикой: 2 324 карточки
в HTML — это мегабайты разметки и секунды на разбор, при том что человек увидит
первые двадцать. Поэтому здесь компактные записи и никаких описаний.

Запускать после build_catalog.py и fetch_images.py — порядок в README.
"""
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "catalog.json"
PHOTOS = ROOT / "site" / "public" / "showcase"
OUT = ROOT / "site" / "public" / "data"

# Сколько брендов показываем вкладками — остальные уходят в «Другие».
# Двенадцать вкладок ещё читаются строкой, тридцать превращаются в кашу.
TOP_BRANDS = 12

# Сопутствующее — не напольное покрытие. Сортировка идёт по цене, и без этого
# каталог открывается четырьмя листами подложки: они просто самые дешёвые.
EXCLUDE_GROUPS = {"Подложка и комплектующие"}

# Часть подложек лежит прямо в группе «Ламинат» — это грязь в выгрузке,
# а не наша ошибка (вопрос по каталогу к клиенту). Отсекаем по названию.
EXCLUDE_NAME = re.compile(r"^\s*подложк", re.IGNORECASE)


def local_photo(item_id: str) -> str | None:
    """Наш WebP, если он скачан. Правило имени — как в fetch_images.py."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", item_id).strip("_")[:80] + ".webp"
    return f"/showcase/{name}" if (PHOTOS / name).exists() else None


def build(direction: str) -> dict:
    items = [
        i for i in json.loads(CATALOG.read_text(encoding="utf-8"))
        if i["direction"] == direction and i["price"] and i["pictures"]
        and i["group"] not in EXCLUDE_GROUPS
        and not EXCLUDE_NAME.match(i["name"])
    ]
    items.sort(key=lambda i: i["price"])

    brands = [b for b, _ in Counter(i["vendor"] for i in items if i["vendor"]).most_common(TOP_BRANDS)]
    groups = [g for g, _ in Counter(i["group"] for i in items).most_common()]

    return {
        "direction": direction,
        "groups": groups,
        "brands": brands,
        "items": [
            {
                "n": i["name"],
                "u": i["url"],
                "p": i["price"],
                # Пустую единицу не заменяем на «м²»: в выгрузке есть позиции,
                # где цена за упаковку, и такая подмена — враньё в цене.
                "e": i["unit"],
                "v": i["vendor"],
                "g": i["group"],
                "s": i["subgroup"],
                "i": local_photo(i["id"]) or i["pictures"][0],
            }
            for i in items
        ],
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for direction in ("poly", "plitka"):
        data = build(direction)
        path = OUT / f"catalog-{direction}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        size = path.stat().st_size / 1024
        print(f"  {direction}: {len(data['items'])} позиций, "
              f"{len(data['brands'])} брендов, {size:.0f} КБ → {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
