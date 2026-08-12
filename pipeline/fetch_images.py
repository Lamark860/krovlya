#!/usr/bin/env python3
"""Шаг 4: качает картинки товаров витрины и кладёт их локально в WebP.

Запуск:  python3 pipeline/fetch_images.py

Зачем локально: тянуть картинки с moiremont18.ru на лендинге нельзя — это чужая
скорость и чужая доступность, а от скорости страницы зависит и цена клика в Директе.

Требуется cwebp (входит в webp, ставится через homebrew). Скачивание с повторами:
канал бывает забит, и одиночные запросы отваливаются по таймауту.
"""
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOWCASE = ROOT / "site" / "src" / "data" / "showcase.json"
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


def main():
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
