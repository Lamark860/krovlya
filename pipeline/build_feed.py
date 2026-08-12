#!/usr/bin/env python3
"""Шаг 2: catalog.json → YML-фиды для Яндекс.Директа (по одному на направление).

Запуск:  python3 pipeline/build_feed.py

Дерево категорий строим своё — из групп, посчитанных на шаге 1, а не из категорий
магазина: в выгрузке два параллельных дерева, и товар ушёл бы в Директ дважды.
Внутри кампании категории фида используются для деления на товарные группы,
поэтому подгруппы (класс, формат) вынесены отдельными узлами.

Результат: data/feeds/*.yml + отчёт с проверками. Без внешних зависимостей.
"""
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "catalog.json"
OUT_DIR = ROOT / "data" / "feeds"

# TODO: «company» уточнить после ответа на блокер Б1 (ИП Дмитриев А.Н. или ООО «Респект»)
SHOP = {
    "name": "Мой ремонт",
    "company": "ООО «Респект»",
    "url": "https://moiremont18.ru",
}

FEEDS = {
    "plitka": {"file": "feed_plitka.yml", "root": "Плитка и керамогранит"},
    "poly": {"file": "feed_poly.yml", "root": "Напольные покрытия"},
}

MAX_URL = 2048
MAX_ID = 100


def build_categories(items, root_name):
    """Возвращает (список категорий для XML, индекс «группа/подгруппа» → id)."""
    categories = [{"id": 1, "name": root_name, "parent": None}]
    index, next_id = {}, 2

    for group in dict.fromkeys(i["group"] for i in items):
        group_id = next_id
        next_id += 1
        categories.append({"id": group_id, "name": group, "parent": 1})
        index[(group, "")] = group_id

        subgroups = dict.fromkeys(i["subgroup"] for i in items if i["group"] == group and i["subgroup"])
        for subgroup in subgroups:
            categories.append({"id": next_id, "name": subgroup, "parent": group_id})
            index[(group, subgroup)] = next_id
            next_id += 1

    return categories, index


def build_feed(direction, items, config):
    catalog = ET.Element("yml_catalog", date=datetime.now().strftime("%Y-%m-%d %H:%M"))
    shop = ET.SubElement(catalog, "shop")
    for tag in ("name", "company", "url"):
        ET.SubElement(shop, tag).text = SHOP[tag]
    currencies = ET.SubElement(shop, "currencies")
    ET.SubElement(currencies, "currency", id="RUB", rate="1")

    categories, index = build_categories(items, config["root"])
    categories_el = ET.SubElement(shop, "categories")
    for category in categories:
        attrs = {"id": str(category["id"])}
        if category["parent"]:
            attrs["parentId"] = str(category["parent"])
        ET.SubElement(categories_el, "category", **attrs).text = category["name"]

    offers_el = ET.SubElement(shop, "offers")
    problems = []
    for item in items:
        if len(item["id"]) > MAX_ID or len(item["url"]) > MAX_URL or not item["url"]:
            problems.append(item["id"])
            continue

        offer = ET.SubElement(offers_el, "offer", id=item["id"],
                              available="true" if item["available"] else "false")
        ET.SubElement(offer, "url").text = item["url"]
        ET.SubElement(offer, "price").text = str(item["price"])
        if item.get("old_price") and item["old_price"] > item["price"]:
            ET.SubElement(offer, "oldprice").text = str(item["old_price"])
        ET.SubElement(offer, "currencyId").text = "RUB"
        ET.SubElement(offer, "categoryId").text = str(
            index.get((item["group"], item["subgroup"])) or index[(item["group"], "")])
        for picture in item["pictures"]:
            ET.SubElement(offer, "picture").text = picture
        ET.SubElement(offer, "name").text = item["name"]
        if item["vendor"]:
            ET.SubElement(offer, "vendor").text = item["vendor"]
        if item["description"]:
            ET.SubElement(offer, "description").text = item["description"]
        if item["unit"]:
            ET.SubElement(offer, "param", name="Цена за").text = item["unit"]
        for name, value in item["params"].items():
            ET.SubElement(offer, "param", name=name).text = value

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / config["file"]
    ET.indent(catalog, space=" ")
    ET.ElementTree(catalog).write(path, encoding="utf-8", xml_declaration=True)

    size_mb = path.stat().st_size / 1024 / 1024
    offers = len(offers_el)
    print(f"\n{path.relative_to(ROOT)}: {offers} офферов, {len(categories)} категорий, {size_mb:.1f} МБ")
    if problems:
        print(f"  ⚠ пропущено из-за id/url: {len(problems)} ({', '.join(problems[:5])}…)")
    print(f"  без картинок: {sum(1 for i in items if not i['pictures'])} | "
          f"без описания: {sum(1 for i in items if not i['description'])} | "
          f"без бренда: {sum(1 for i in items if not i['vendor'])} | "
          f"нет в наличии: {sum(1 for i in items if not i['available'])}")
    for category in categories:
        if category["parent"]:
            count = sum(1 for i in items
                        if index.get((i["group"], i["subgroup"]), index.get((i["group"], ""))) == category["id"])
            print(f"    {count:5d}  {category['name']}")


def main():
    items = json.loads(CATALOG.read_text(encoding="utf-8"))
    for direction, config in FEEDS.items():
        chunk = [i for i in items if i["direction"] == direction]
        if chunk:
            build_feed(direction, chunk, config)


if __name__ == "__main__":
    main()
