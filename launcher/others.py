"""Другие лаунчеры: для них мы только обновляем файлы сборки в папке профиля, игру запускает сам лаунчер."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List


def modrinth_app_dir() -> Path:
    """Папка данных Modrinth App (по документации Modrinth)."""
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "ModrinthApp"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ModrinthApp"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "ModrinthApp"


def modrinth_profiles() -> List[Path]:
    """Профили Modrinth App: подпапки <папка данных>/profiles. Пусто, если приложение не найдено."""
    d = modrinth_app_dir() / "profiles"
    try:
        return sorted(p for p in d.iterdir() if p.is_dir())
    except OSError:
        return []


def unsafe_folder(p: Path) -> str:
    """Не даём направить лаунчер на корень диска, домашнюю папку и подобные места. Возвращает причину или ''."""
    try:
        r = p.resolve()
    except OSError:
        return "не удалось прочитать путь"
    home = Path.home().resolve()
    if r == Path(r.anchor):
        return "это корень диска"
    if r == home or r.parent == home and r.name.lower() in (
            "desktop", "documents", "downloads", "рабочий стол", "документы", "загрузки"):
        return "это личная папка, а не папка профиля игры"
    if r.name.lower() in ("windows", "system32", "program files", "program files (x86)"):
        return "это системная папка"
    return ""
