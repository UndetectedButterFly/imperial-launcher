"""Настройки лаунчера (хранятся в профиле пользователя, а не рядом с exe)."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import List

# >>> ЗАМЕНИТЕ на свои репозитории перед сборкой (или задайте переменными окружения) <<<
DEFAULT_PACK_REPO = os.environ.get("IMPERIAL_PACK_REPO", "OWNER/imperial-pack")
DEFAULT_LAUNCHER_REPO = os.environ.get("IMPERIAL_LAUNCHER_REPO", "OWNER/imperial-launcher")


def app_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", str(Path.home())))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    d = base / "ImperialLauncher"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_root() -> Path:
    return Path.home() / "ImperialMC"


@dataclass
class Settings:
    pack_repo: str = DEFAULT_PACK_REPO
    launcher_repo: str = DEFAULT_LAUNCHER_REPO
    root_dir: str = ""              # папка установки; Freesm лежит в <root>/Freesm
    play_kind: str = "freesm"       # freesm — Freesm/Prism (с кнопкой «Играть»); folder — только обновить папку
    folder_dir: str = ""            # папка профиля/игры для режима folder (например, профиль Modrinth App)
    freesm_dir: str = ""            # (устарело) папка с Freesm; оставлено для старых настроек
    freesm_exe: str = ""            # путь к FreesmLauncher.exe уже установленного Freesm
    instances_dir: str = ""         # папка instances, где лежат экземпляры (для обычной установки Freesm)
    instance_id: str = ""           # пусто — берём из манифеста
    nickname: str = ""
    account_mode: str = "offline"   # offline | microsoft
    java_path: str = ""
    memory_mb: int = 6144
    manage_memory: bool = False     # False — память экземпляра не трогаем (при создании ставится рекомендованная)
    join_server: bool = True
    quarantine_unknown: bool = False
    auto_prelaunch: bool = False
    extra_allowed_mods: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.root_dir:
            self.root_dir = str(default_root())

    @property
    def manifest_url(self) -> str:
        return f"https://github.com/{self.pack_repo}/releases/latest/download/manifest.json"

    @property
    def freesm_root(self) -> Path:
        return Path(self.freesm_dir) if self.freesm_dir else Path(self.root_dir) / "Freesm"


def _file() -> Path:
    return app_dir() / "settings.json"


def load() -> Settings:
    s = Settings()
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return s
    known = {f.name for f in fields(Settings)}
    for k, v in data.items():
        if k in known and k not in ("pack_repo", "launcher_repo"):
            setattr(s, k, v)
    # репозитории из файла используем только если пользователь менял их вручную
    for k in ("pack_repo", "launcher_repo"):
        if data.get(k) and not str(data[k]).startswith("OWNER/"):
            setattr(s, k, data[k])
    return s


def save(s: Settings) -> None:
    tmp = _file().with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(s), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _file())
