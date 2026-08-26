#!/usr/bin/env python3
"""Ручная выкатка на виртуальный хостинг по FTP.

Запасной путь на случай, когда автодеплой недоступен: секреты не заданы,
Actions не настроены, или нужно залить срочную правку в обход пайплайна.

Сборка сюда не входит — на вход подаётся готовый каталог релиза:

    cd site && bun run build              # либо в контейнере, см. README
    mkdir -p release && cp -R site/dist/. release/
    cp deploy/htaccess release/.htaccess
    mkdir -p release/api && cp api-php/index.php release/api/index.php

    FTP_HOST=... FTP_USER=... FTP_PASSWORD=... FTP_PATH=www/example.com \\
        python3 deploy/upload_ftp.py release

Пароль только через окружение: репозиторий публичный.

Чего скрипт намеренно НЕ делает — не удаляет на сервере ничего. Файлы, которых
больше нет в релизе, остаются висеть; чистить их надо руками. Это осознанный
размен: снести лишнее в корне сайта проще, чем заметить, что снесено нужное.
"""
import os
import sys
from ftplib import FTP


def env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        sys.exit(f"не задана переменная окружения {name}")
    return value


def connect() -> FTP:
    ftp = FTP()
    ftp.connect(env("FTP_HOST"), int(os.environ.get("FTP_PORT", "21")), timeout=120)
    ftp.login(env("FTP_USER"), env("FTP_PASSWORD"))
    return ftp


def remote_sizes(ftp: FTP, remote_dir: str) -> dict[str, int]:
    """Что уже лежит в каталоге и какого размера.

    Нужно для докачки: сессия рвётся на середине релиза (проверено на reg.ru —
    таймаут после сотни файлов), и заливать заново все 420 значит с большой
    вероятностью оборваться там же.
    """
    sizes: dict[str, int] = {}
    try:
        for name, facts in ftp.mlsd(remote_dir, facts=["type", "size"]):
            if facts.get("type") == "file":
                sizes[name] = int(facts.get("size", -1))
    except Exception:
        pass    # MLSD может быть не включён — тогда просто зальём всё заново
    return sizes


def upload(ftp: FTP, local_root: str, remote_root: str) -> tuple[FTP, int, int, int]:
    files = dirs = skipped = 0
    for root, _, filenames in os.walk(local_root):
        rel = os.path.relpath(root, local_root)
        remote_dir = remote_root if rel == "." else f"{remote_root}/{rel}"
        if rel != ".":
            try:
                ftp.mkd(remote_dir)
                dirs += 1
            except Exception:
                pass  # каталог уже есть — обычное дело при повторной выкатке

        existing = remote_sizes(ftp, remote_dir)

        for name in filenames:
            local_path = os.path.join(root, name)
            size = os.path.getsize(local_path)
            # Совпал размер — файл уже долит. Содержимое не сверяем: разные файлы
            # одного байта в байт размера здесь не встречаются, а лишний RETR
            # на каждый файл удвоил бы время выкатки.
            if existing.get(name) == size:
                skipped += 1
                continue

            # Обрыв на конкретном файле — не повод терять уже залитое:
            # переподключаемся и повторяем тот же файл.
            for attempt in range(1, 4):
                try:
                    with open(local_path, "rb") as f:
                        ftp.storbinary(f"STOR {remote_dir}/{name}", f, blocksize=256 * 1024)
                    break
                except Exception as problem:
                    if attempt == 3:
                        raise
                    print(f"  обрыв на {name} ({problem}), переподключаюсь — попытка {attempt + 1}")
                    try:
                        ftp.close()
                    except Exception:
                        pass
                    ftp = connect()

            files += 1
            if files % 25 == 0:
                print(f"  залито файлов: {files}")
    return ftp, files, dirs, skipped


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.exit("использование: upload_ftp.py <каталог-релиза>")
    local_root = argv[1]
    if not os.path.isdir(local_root):
        sys.exit(f"нет такого каталога: {local_root}")

    ftp = connect()
    remote_root = env("FTP_PATH")
    print(f"заливаю {local_root} → {remote_root}")
    ftp, files, dirs, skipped = upload(ftp, local_root, remote_root)
    print(f"готово: {files} файлов, {dirs} новых каталогов, {skipped} уже были на месте")

    try:
        ftp.quit()
    except Exception:
        ftp.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
