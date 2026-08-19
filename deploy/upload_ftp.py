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


def upload(ftp: FTP, local_root: str, remote_root: str) -> tuple[int, int]:
    files = dirs = 0
    for root, _, filenames in os.walk(local_root):
        rel = os.path.relpath(root, local_root)
        remote_dir = remote_root if rel == "." else f"{remote_root}/{rel}"
        if rel != ".":
            try:
                ftp.mkd(remote_dir)
                dirs += 1
            except Exception:
                pass  # каталог уже есть — обычное дело при повторной выкатке
        for name in filenames:
            with open(os.path.join(root, name), "rb") as f:
                ftp.storbinary(f"STOR {remote_dir}/{name}", f, blocksize=256 * 1024)
            files += 1
            if files % 25 == 0:
                print(f"  залито файлов: {files}")
    return files, dirs


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.exit("использование: upload_ftp.py <каталог-релиза>")
    local_root = argv[1]
    if not os.path.isdir(local_root):
        sys.exit(f"нет такого каталога: {local_root}")

    ftp = FTP()
    ftp.connect(env("FTP_HOST"), int(os.environ.get("FTP_PORT", "21")), timeout=60)
    ftp.login(env("FTP_USER"), env("FTP_PASSWORD"))

    remote_root = env("FTP_PATH")
    print(f"заливаю {local_root} → {remote_root}")
    files, dirs = upload(ftp, local_root, remote_root)
    print(f"готово: {files} файлов, {dirs} новых каталогов")

    ftp.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
