#!/usr/bin/env python3
"""Шаг 4: качает картинки товаров и кладёт их локально в WebP.

Запуск:
    python3 pipeline/fetch_images.py                          # витрина лендингов
    python3 pipeline/fetch_images.py --catalog plitka --limit 300

Зачем локально: тянуть картинки с moiremont18.ru на лендинге нельзя — это чужая
скорость и чужая доступность, а от скорости страницы зависит и цена клика в Директе.

Режим --catalog добавлен под вторую посадочную. Каталог — рекламная посадочная,
а 98 % его карточек грузят фото с сайта магазина, который заказчик сам называет
сломанным (вопрос 55). Качать все 5 000 разом — полдня и 335 МБ, поэтому берём
верх выдачи: каталог отсортирован по цене, и до кнопки «Показать ещё» человек
видит первые два-три десятка. --limit 300 закрывает первые страницы с запасом.

Требуется cwebp (входит в webp, ставится через homebrew). Скачивание с повторами:
канал бывает забит, и одиночные запросы отваливаются по таймауту.
"""
import argparse
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

# Лежит рядом: при запуске «python3 pipeline/fetch_images.py»
# каталог скрипта и так первый в sys.path.
from filters import is_product

ROOT = Path(__file__).resolve().parent.parent
SHOWCASE = ROOT / "site" / "src" / "data" / "showcase.json"
CATALOG = ROOT / "data" / "catalog.json"
OUT_DIR = ROOT / "site" / "public" / "showcase"
WIDTH = 900        # хватает и для карточки подборки, и для крупного блока
QUALITY = 80
RETRIES = 4
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36"


def safe_name(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")[:80]


def download(url, dest):
    for attempt in range(1, RETRIES + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(request, timeout=45) as response:
                dest.write_bytes(response.read())
            return True
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if attempt == RETRIES:
                print(f"    ✗ не скачалось: {url} ({error})")
                return False
            time.sleep(3 * attempt)
    return False


def to_webp(remote, item_id, tmp):
    """Качает и жмёт одну картинку. Возвращает путь для страницы или None."""
    name = f"{safe_name(item_id)}.webp"
    target = OUT_DIR / name
    if target.exists():
        return f"/showcase/{name}", True     # уже была
    if not download(remote, tmp):
        return None, False
    result = subprocess.run(
        ["cwebp", "-quiet", "-q", str(QUALITY), "-resize", str(WIDTH), "0",
         str(tmp), "-o", str(target)],
        capture_output=True,
    )
    if result.returncode != 0:
        print(f"    ✗ cwebp: {result.stderr.decode()[:120]}")
        return None, False
    return f"/showcase/{name}", False


def fetch_catalog(direction, limit):
    """Верх выдачи каталога: те карточки, которые человек видит до «Показать ещё».

    Сортировка та же, что на странице (по цене), и тот же фильтр сопутствующего —
    иначе скачаются мешки клея, которых в каталоге всё равно нет.
    Файлы кладутся рядом с витриной: build_catalog_page.py ищет их по тому же
    правилу имени и сам подставит локальный путь на следующем шаге.
    """
    items = [i for i in json.loads(CATALOG.read_text(encoding="utf-8"))
             if i["direction"] == direction and i["price"] and i["pictures"] and is_product(i)]
    items.sort(key=lambda i: i["price"])
    items = items[:limit]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT_DIR / "_tmp"
    ok = skipped = failed = 0
    for n, item in enumerate(items, 1):
        path, was_there = to_webp(item["pictures"][0], item["id"], tmp)
        if path and was_there:
            skipped += 1
        elif path:
            ok += 1
        else:
            failed += 1
        if n % 50 == 0:
            print(f"  … {n} из {len(items)}: скачано {ok}, было {skipped}, не вышло {failed}")
    if tmp.exists():
        tmp.unlink()

    weight = sum(f.stat().st_size for f in OUT_DIR.glob("*.webp")) / 1024 / 1024
    print(f"каталог «{direction}»: скачано {ok}, уже было {skipped}, не вышло {failed} "
          f"(из {len(items)} верхних по цене)")
    print(f"итого в {OUT_DIR.relative_to(ROOT)}: {len(list(OUT_DIR.glob('*.webp')))} файлов, {weight:.0f} МБ")
    print("⚠ остальные карточки каталога по-прежнему грузят фото с moiremont18.ru (вопрос 55)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", choices=("poly", "plitka"),
                        help="качать верх выдачи каталога, а не витрину")
    parser.add_argument("--limit", type=int, default=300,
                        help="сколько позиций каталога брать (по умолчанию 300)")
    args = parser.parse_args()
    if args.catalog:
        return fetch_catalog(args.catalog, args.limit)

    data = json.loads(SHOWCASE.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT_DIR / "_tmp"
    total = ok = skipped = 0

    for direction, blocks in data.items():
        for block in blocks:
            for item in block["items"]:
                total += 1
                remote = item.get("picture_remote") or item["picture"]
                name = f"{safe_name(item['id'])}.webp"
                target = OUT_DIR / name

                if target.exists():
                    skipped += 1
                elif download(remote, tmp):
                    result = subprocess.run(
                        ["cwebp", "-quiet", "-q", str(QUALITY), "-resize", str(WIDTH), "0",
                         str(tmp), "-o", str(target)],
                        capture_output=True,
                    )
                    if result.returncode != 0:
                        print(f"    ✗ cwebp: {result.stderr.decode()[:120]}")
                        continue
                else:
                    continue

                item["picture_remote"] = remote
                item["picture"] = f"/showcase/{name}"
                ok += 1

    if tmp.exists():
        tmp.unlink()
    SHOWCASE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    weight = sum(f.stat().st_size for f in OUT_DIR.glob("*.webp")) / 1024
    print(f"готово: {ok} из {total} (пропущено как уже скачанные: {skipped})")
    print(f"итого в {OUT_DIR.relative_to(ROOT)}: {len(list(OUT_DIR.glob('*.webp')))} файлов, "
          f"{weight:.0f} КБ, в среднем {weight / max(len(list(OUT_DIR.glob('*.webp'))), 1):.0f} КБ")


if __name__ == "__main__":
    main()
