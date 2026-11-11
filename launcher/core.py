"""Сценарии высокого уровня: «установить/обновить/играть». Используются и окном, и командной строкой."""
from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

from . import __version__, freesm, javafind, net, others, syncer
from .manifest import Manifest, load_manifest, version_tuple
from .settings import Settings, app_dir

Log = Callable[[str], None]
Progress = Callable[[int, int, str], None]


class CoreError(Exception):
    """Понятная пользователю ошибка (показываем текст как есть)."""


@dataclass
class Paths:
    exe: Optional[Path]            # FreesmLauncher.exe (None — Freesm ещё не установлен)
    instances_dir: Path            # папка, где лежат экземпляры
    data_dir: Optional[Path]       # корень данных для флага -d; None — Freesm не portable
    portable: bool

    @property
    def accounts_root(self) -> Path:
        """Где искать accounts.json: рядом с instances (так лежит у portable и у обычной установки)."""
        return self.data_dir or self.instances_dir.parent


def setup_logging() -> logging.Logger:
    lg = logging.getLogger("imperial")
    if not lg.handlers:
        lg.setLevel(logging.INFO)
        h = logging.handlers.RotatingFileHandler(app_dir() / "launcher.log", maxBytes=500_000,
                                                 backupCount=2, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        lg.addHandler(h)
    return lg


def check_launcher_update(s: Settings) -> Optional[Tuple[str, str]]:
    """Возвращает (версия, ссылка), если на GitHub есть более новый релиз лаунчера."""
    try:
        rel = net.get_json(f"https://api.github.com/repos/{s.launcher_repo}/releases/latest", timeout=10)
        tag = str(rel.get("tag_name", ""))
        if tag and version_tuple(tag) > version_tuple(__version__):
            return tag, str(rel.get("html_url", f"https://github.com/{s.launcher_repo}/releases"))
    except Exception:
        pass
    return None


def fetch_manifest(s: Settings) -> Manifest:
    try:
        m = load_manifest(s.manifest_url)
    except Exception as e:
        raise CoreError(f"Не удалось получить список файлов сборки ({s.pack_repo}): {e}")
    if version_tuple(m.min_launcher) > version_tuple(__version__):
        raise CoreError(f"Сборке нужен лаунчер версии {m.min_launcher} или новее "
                        f"(у вас {__version__}). Скачайте свежий на странице релизов.")
    if s.instance_id:
        m.instance_id = s.instance_id
    return m


def resolve_paths(s: Settings) -> Paths:
    """Определяет, где Freesm и экземпляры. Выбор пользователя (exe, instances) важнее автоопределения."""
    exe: Optional[Path] = None
    if s.freesm_exe:
        p = Path(s.freesm_exe)
        exe = p if p.is_file() else freesm.find_freesm_exe(p)
        if exe is None:
            raise CoreError(f"Не найден файл Freesm: {s.freesm_exe}. Выберите FreesmLauncher.exe заново "
                            f"в «Настройках» или очистите поле, чтобы лаунчер поставил свою копию.")
    elif s.freesm_dir:
        exe = freesm.find_freesm_exe(Path(s.freesm_dir))
    else:
        exe = freesm.find_freesm_exe(Path(s.root_dir) / "Freesm")
    custom = Path(s.instances_dir) if s.instances_dir else None

    if exe is None:  # будущая portable-установка
        base = Path(s.freesm_dir) if s.freesm_dir else Path(s.root_dir) / "Freesm"
        return Paths(None, custom or base / "instances", base, True)

    portable = freesm.is_portable_layout(exe)
    if custom:
        inst = custom
    elif portable:
        inst = exe.parent / "instances"
    else:
        inst = freesm.guess_instances_dir(exe)
        if inst is None:
            raise CoreError(
                "Freesm установлен как обычная программа, и я не смог найти папку с экземплярами. Укажите её в "
                "«Настройках» → «Папка instances». Подсказка: в Freesm нажмите правой кнопкой на экземпляр → "
                "«Папка»: откроется папка экземпляра, а нужная лежит на уровень выше (подойдёт и она сама).")
    return Paths(exe, inst, exe.parent if portable else None, portable)


def folder_game_dir(s: Settings) -> Path:
    """Папка профиля для режима «только обновить» (Modrinth App и другие лаунчеры)."""
    if not s.folder_dir.strip():
        raise CoreError("Укажите папку профиля: «Настройки» → «Папка профиля или игры». Для Modrinth App: "
                        "откройте профиль → значок папки → скопируйте путь.")
    p = Path(s.folder_dir.strip().strip('"'))
    if not p.is_dir():
        raise CoreError(f"Папка не найдена: {p}")
    bad = others.unsafe_folder(p)
    if bad:
        raise CoreError(f"Эту папку выбирать нельзя: {bad}. Нужна папка именно профиля игры (где лежит mods).")
    return p


def paths_for(s: Settings, m: Manifest) -> Tuple[Paths, Path, Path]:
    """(пути Freesm, папка экземпляра, папка игры)."""
    if s.play_kind == "folder":
        g = folder_game_dir(s)
        return Paths(None, g, None, False), g, g
    paths = resolve_paths(s)
    inst = paths.instances_dir / m.instance_id
    return paths, inst, freesm.game_dir_of(inst)


def status(s: Settings, m: Optional[Manifest]) -> dict:
    if s.play_kind == "folder":
        try:
            game = folder_game_dir(s)
        except CoreError:
            return {"freesm_installed": True, "instance_exists": False, "installed_version": "",
                    "latest_version": m.version if m else ""}
        return {"freesm_installed": True, "instance_exists": True,
                "installed_version": syncer.installed_version(game), "latest_version": m.version if m else ""}
    inst_id = (m.instance_id if m else s.instance_id) or "ImperialPack"
    try:
        paths = resolve_paths(s)
    except CoreError:
        return {"freesm_installed": False, "instance_exists": False, "installed_version": "", "latest_version": ""}
    inst = paths.instances_dir / inst_id
    game = freesm.game_dir_of(inst)
    return {
        "freesm_installed": paths.exe is not None,
        "instance_exists": freesm.instance_exists(inst),
        "installed_version": syncer.installed_version(game) if game.exists() else "",
        "latest_version": m.version if m else "",
    }


def sync_options(s: Settings) -> syncer.SyncOptions:
    return syncer.SyncOptions(quarantine_unknown=s.quarantine_unknown,
                              extra_allowed=list(s.extra_allowed_mods))


def auto_java(manifest: Manifest) -> Optional[javafind.JavaInfo]:
    return javafind.best_for(javafind.find_javas(), manifest.java_major)


def run_folder_update(s: Settings, log: Log, progress: Progress, cancel: Optional[threading.Event] = None,
                      manifest: Optional[Manifest] = None) -> syncer.SyncResult:
    """Только обновить файлы сборки в выбранной папке. Ничего не запускаем и настройки лаунчера не трогаем."""
    m = manifest or fetch_manifest(s)
    game = folder_game_dir(s)
    log(f"Сборка «{m.name or m.pack}» {m.version}. Обновляю папку: {game}")
    log(f"ℹ В вашем лаунчере у профиля должны стоять Minecraft {m.minecraft} и {m.loader_type} "
        f"{m.loader_version}, Java {m.java_major}. Это настраивается там, лаунчер этого не меняет.")
    res = syncer.sync(m, game, sync_options(s), log=log, progress=progress, cancel=cancel)
    if res.ok:
        log("Готово. Запустите игру из вашего лаунчера.")
    return res


def run_update(s: Settings, log: Log, progress: Progress,
               cancel: Optional[threading.Event] = None,
               play: bool = False, manifest: Optional[Manifest] = None) -> syncer.SyncResult:
    if s.play_kind == "folder":
        return run_folder_update(s, log, progress, cancel, manifest)
    m = manifest or fetch_manifest(s)
    if s.instance_id:           # выбор пользователя важнее, даже если манифест получен раньше
        m.instance_id = s.instance_id
    log(f"Сборка «{m.name or m.pack}» {m.version} · Minecraft {m.minecraft} · {m.loader_type} {m.loader_version}")

    paths = resolve_paths(s)
    if paths.exe is None:
        log("Freesm Launcher не найден, устанавливаю…")
        try:
            freesm.install_freesm(paths.data_dir, log, progress, cancel)
        except freesm.FreesmError as e:
            raise CoreError(str(e))
        except net.DownloadError as e:
            raise CoreError(f"Не удалось скачать Freesm: {e}")
        paths = resolve_paths(s)

    running_names = (paths.exe.name,) if paths.exe else ("FreesmLauncher.exe", "prismlauncher.exe")
    if freesm.is_running(running_names):
        raise CoreError("Freesm сейчас запущен. Закройте его (и игру) и повторите: лаунчер меняет "
                        "файлы сборки и настройки экземпляра.")

    inst = freesm.ensure_instance(paths.instances_dir, m, log)
    mem_max = int(s.memory_mb) if s.manage_memory else 0   # 0 — память экземпляра не трогаем
    mem_min = min(m.memory_min, mem_max) if mem_max else 0
    done = freesm.apply_instance_settings(
        inst, java_path=s.java_path, mem_min=mem_min, mem_max=mem_max,
        prelaunch_exe=_self_exe(), enable_prelaunch=s.auto_prelaunch)
    for line in done:
        log("✓ " + line)

    game = freesm.game_dir_of(inst)
    res = syncer.sync(m, game, sync_options(s), log=log, progress=progress, cancel=cancel)

    if play:
        if not res.ok:
            raise CoreError("Сборка обновилась с ошибками, запуск отменён. Подробности в журнале.")
        launch_game(s, m, paths, log)
    return res


def launch_game(s: Settings, m: Manifest, paths: Paths, log: Log) -> None:
    profile = ""
    if s.account_mode == "offline":
        profile = s.nickname.strip()
        if not profile:
            raise CoreError("Укажите ник в настройках (вкладка «Игрок»).")
        names = freesm.account_names(paths.accounts_root)
        if not names or profile.lower() not in (n.lower() for n in names):
            # не блокируем запуск: формат accounts.json мог измениться, и решает сам Freesm
            log(f"⚠ Не удалось подтвердить аккаунт «{profile}» в Freesm. Если игра не запустится: "
                f"Freesm → Аккаунты → «Добавить оффлайн» с этим ником.")
    server = m.server_address if s.join_server else ""
    log("Запускаю игру через Freesm…")
    assert paths.exe is not None
    freesm.launch(paths.exe, paths.data_dir, m.instance_id, profile, server)


def _self_exe() -> Optional[str]:
    if getattr(sys, "frozen", False):
        return sys.executable
    return None


def run_headless_sync(s: Settings, instance_path: str, log: Log) -> bool:
    """Для PreLaunchCommand Freesm: тихо обновить сборку. Никогда не бросает исключений."""
    try:
        m = fetch_manifest(s)
        game = freesm.game_dir_of(Path(instance_path))
        res = syncer.sync(m, game, sync_options(s), log=log)
        return res.ok
    except Exception as e:  # не мешаем запуску игры
        log(f"Автообновление пропущено: {e}")
        return False
