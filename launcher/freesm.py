"""Работа с Freesm Launcher (форк Prism Launcher): установка, экземпляр, Java/память, запуск.

Что здесь проверено по документации Prism (Freesm — его форк с теми же флагами):
  -d <dir>   корень данных лаунчера;  -l <id>  запуск экземпляра (id = имя его папки);
  -s <addr>  зайти на сервер;          -a <profile>  аккаунт по имени профиля.
Ключи instance.cfg: OverrideJavaLocation / JavaPath / OverrideMemory / MinMemAlloc / MaxMemAlloc,
OverrideCommands / PreLaunchCommand. Файлы instance.cfg и mmc-pack.json — как у Prism;
перед правкой делается копия *.imperial-bak.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from threading import Event
from typing import Callable, Dict, List, Optional, Tuple

from . import net
from .manifest import Manifest

FREESM_REPO = "FreesmTeam/FreesmLauncher"
IS_WIN = sys.platform.startswith("win")
EXE_NAMES = ("FreesmLauncher.exe", "prismlauncher.exe")   # Freesm — форк Prism, флаги и форматы те же
_ALL_EXE_LOWER = {"freesmlauncher.exe", "prismlauncher.exe"}
PRELAUNCH_MARK = "--sync"
_NO_WINDOW = 0x08000000 if IS_WIN else 0


class FreesmError(Exception):
    pass


# ---------- скачивание и установка Freesm ----------

def latest_release() -> dict:
    return net.get_json(f"https://api.github.com/repos/{FREESM_REPO}/releases/latest")


def pick_windows_asset(release: dict) -> dict:
    """Ищет portable-архив для Windows x64 (не arm64, не установщик)."""
    best, best_score = None, -1
    for a in release.get("assets", []):
        n = str(a.get("name", "")).lower()
        if "windows" not in n or not n.endswith(".zip"):
            continue
        score = 0
        if "portable" in n:
            score += 10
        if "arm64" in n:
            score -= 100
        if "msvc" in n:
            score += 2
        if score > best_score:
            best, best_score = a, score
    if best is None or best_score < 0:
        names = ", ".join(str(a.get("name")) for a in release.get("assets", [])) or "нет файлов"
        raise FreesmError(
            "В последнем релизе Freesm нет portable-архива для Windows x64. "
            f"Файлы релиза: {names}. Установите Freesm вручную и укажите его папку в настройках.")
    return best


def find_freesm_exe(base: Path) -> Optional[Path]:
    base = Path(base)
    if not base.is_dir():
        return None
    for name in EXE_NAMES:                       # сначала Freesm, потом Prism
        for p in (base / name, base / name.replace("freesmlauncher", "FreesmLauncher")
                  .replace("prismlauncher", "PrismLauncher")):
            if p.is_file():
                return p
    for want in EXE_NAMES:
        for root, dirs, files in os.walk(base):
            if len(Path(root).relative_to(base).parts) > 2:
                dirs[:] = []
                continue
            for f in files:
                if f.lower() == want.lower():
                    return Path(root) / f
    return None


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    root = dest.resolve()
    for info in zf.infolist():
        target = (root / info.filename).resolve()
        if target != root and root not in target.parents:
            raise FreesmError(f"небезопасный путь в архиве: {info.filename}")
    zf.extractall(root)


def install_freesm(dest_dir: Path, log: Callable[[str], None],
                   progress: Callable[[int, int, str], None],
                   cancel: Optional[Event] = None,
                   release: Optional[dict] = None) -> Path:
    """Скачивает и распаковывает Freesm в dest_dir. Возвращает путь к exe."""
    dest_dir = Path(dest_dir)
    log("Узнаю последнюю версию Freesm…")
    rel = release or latest_release()
    asset = pick_windows_asset(rel)
    log(f"Скачиваю {asset['name']}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    zpath = dest_dir / f".dl-{asset['name']}"
    total = int(asset.get("size") or 0)
    got = [0]

    def on_bytes(n: int) -> None:
        got[0] += n
        progress(max(got[0], 0), total, "Freesm Launcher")

    net.download_file(asset["browser_download_url"], zpath, size=total or None,
                      on_bytes=on_bytes, cancel=cancel, timeout=60)
    try:
        with zipfile.ZipFile(zpath) as zf:
            _safe_extract(zf, dest_dir)
    finally:
        try:
            zpath.unlink()
        except OSError:
            pass
    exe = find_freesm_exe(dest_dir)
    if exe is None:
        raise FreesmError("После распаковки не найден FreesmLauncher.exe")
    portable = exe.parent / "portable.txt"
    if not portable.exists():
        portable.write_text("", encoding="utf-8")  # режим portable: все данные рядом
    log(f"Freesm установлен: {exe.parent}")
    return exe


# ---------- экземпляр ----------

def instance_dir(root: Path, instance_id: str) -> Path:
    return Path(root) / "instances" / instance_id


def game_dir_of(inst: Path) -> Path:
    for n in (".minecraft", "minecraft"):
        if (inst / n).is_dir():
            return inst / n
    return inst / ".minecraft"


def list_instances(root: Path) -> List[str]:
    """Имена папок экземпляров (id) в <root>/instances, где есть instance.cfg."""
    d = Path(root) / "instances"
    try:
        return sorted(p.name for p in d.iterdir() if (p / "instance.cfg").is_file())
    except OSError:
        return []


def is_portable_layout(exe: Path) -> bool:
    """Portable-копия: рядом с exe лежит portable.txt или папка instances."""
    base = Path(exe).parent
    return (base / "portable.txt").exists() or (base / "instances").is_dir()


def detect_installed_exe() -> Optional[Path]:
    """Ищет FreesmLauncher.exe в типичных местах обычной установки (по возможности)."""
    if not IS_WIN:
        return None
    bases = [os.environ.get(v) for v in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")]
    local = os.environ.get("LOCALAPPDATA")
    cands: List[Path] = []
    for b in bases:
        if not b:
            continue
        for sub in ("Programs/FreesmLauncher", "Programs/Freesm Launcher", "FreesmLauncher", "Freesm Launcher"):
            cands.append(Path(b) / sub / "FreesmLauncher.exe")
        for sub in ("Programs/PrismLauncher", "PrismLauncher"):
            cands.append(Path(b) / sub / "prismlauncher.exe")
    if local:
        cands.append(Path(local) / "FreesmLauncher" / "FreesmLauncher.exe")
    for c in cands:
        if c.is_file():
            return c
    return None


def guess_instances_dir(exe: Path) -> Optional[Path]:
    """Где, скорее всего, лежат экземпляры. None — не удалось угадать, пусть укажет пользователь."""
    exe = Path(exe)
    if is_portable_layout(exe):
        return exe.parent / "instances"
    for var in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if not base:
            continue
        for name in ("FreesmLauncher", "Freesm Launcher", "freesmlauncher", "PrismLauncher", "prismlauncher"):
            d = Path(base) / name / "instances"
            if d.is_dir():
                return d
    return None


def instance_exists(inst: Path) -> bool:
    return (inst / "instance.cfg").is_file()


def _posix(p) -> str:
    """Путь со слэшами вперёд: Prism хранит так, а обратные слэши в .cfg пришлось бы экранировать."""
    return str(p).replace("\\", "/")


def _backup_once(p: Path) -> None:
    bak = p.with_name(p.name + ".imperial-bak")
    if p.exists() and not bak.exists():
        shutil.copy2(p, bak)


def ini_set(path: Path, updates: Dict[str, str], section: str = "General") -> None:
    """Меняет/добавляет ключи в секции INI, не трогая остальное и сохраняя перевод строк."""
    path = Path(path)
    if path.exists():
        with open(path, "r", encoding="utf-8-sig", newline="") as f:  # newline="" — не терять CRLF
            text = f.read()
    else:
        text = f"[{section}]\n"
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    header = f"[{section}]"
    start, end = None, len(lines)
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            if s == header:
                start = i
            elif start is not None:
                end = i
                break
    if start is None:
        lines.insert(0, header)
        start, end = 0, len(lines)
    for key, val in updates.items():
        idx = None
        for i in range(start + 1, end):
            if lines[i].split("=", 1)[0].strip() == key and "=" in lines[i]:
                idx = i
                break
        new = f"{key}={val}"
        if idx is not None:
            lines[idx] = new
        else:
            lines.insert(end, new)
            end += 1
    _backup_once(path)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(nl.join(lines) + nl)


def ini_get(path: Path, key: str, section: str = "General") -> Optional[str]:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError:
        return None
    inside = False
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            inside = s == f"[{section}]"
        elif inside and "=" in ln and ln.split("=", 1)[0].strip() == key:
            return ln.split("=", 1)[1].strip()
    return None


def _mmc_pack(mc: str, loader_type: str, loader_ver: str) -> dict:
    comps = [
        {"cachedName": "LWJGL 3", "cachedVersion": "3.3.3", "cachedVolatile": True,
         "dependencyOnly": True, "uid": "org.lwjgl3", "version": "3.3.3"},
        {"cachedName": "Minecraft", "cachedRequires": [{"suggests": "3.3.3", "uid": "org.lwjgl3"}],
         "cachedVersion": mc, "important": True, "uid": "net.minecraft", "version": mc},
    ]
    uid = {"neoforge": "net.neoforged", "forge": "net.minecraftforge",
           "fabric": "net.fabricmc.fabric-loader", "quilt": "org.quiltmc.quilt-loader"}.get(loader_type)
    if uid:
        comps.append({"cachedName": loader_type.capitalize() if loader_type != "neoforge" else "NeoForge",
                      "cachedRequires": [{"equals": mc, "uid": "net.minecraft"}],
                      "cachedVersion": loader_ver, "uid": uid, "version": loader_ver})
    return {"components": comps, "formatVersion": 1}


def ensure_instance(instances_dir: Path, m: Manifest, log: Callable[[str], None]) -> Path:
    """Создаёт экземпляр, если его нет; если сменились версии Minecraft/загрузчика — обновляет mmc-pack.json."""
    inst = Path(instances_dir) / m.instance_id
    inst.mkdir(parents=True, exist_ok=True)
    game_dir_of(inst).mkdir(parents=True, exist_ok=True)
    cfg = inst / "instance.cfg"
    if not cfg.exists():
        text = ("[General]\nConfigVersion=1.2\nInstanceType=OneSix\n"
                f"iconKey=default\nname={m.name or m.instance_id}\n")
        if m.memory_max:  # у НОВОГО экземпляра ставим рекомендованную память; у существующих не трогаем
            text += f"OverrideMemory=true\nMinMemAlloc={m.memory_min}\nMaxMemAlloc={m.memory_max}\n"
        cfg.write_text(text, encoding="utf-8")
        log(f"Создан экземпляр «{m.name or m.instance_id}»")
    pack = inst / "mmc-pack.json"
    want = _mmc_pack(m.minecraft, m.loader_type, m.loader_version) if m.minecraft else None
    if want is not None:
        if not pack.exists():
            pack.write_text(json.dumps(want, indent=4), encoding="utf-8")
            log(f"Версии: Minecraft {m.minecraft}, {m.loader_type} {m.loader_version}")
        else:
            try:
                cur = json.loads(pack.read_text(encoding="utf-8"))
                changed = False
                for c in cur.get("components", []):
                    for w in want["components"]:
                        if c.get("uid") == w["uid"] and c.get("version") != w["version"]:
                            c["version"] = w["version"]
                            c["cachedVersion"] = w["cachedVersion"]
                            changed = True
                if changed:
                    _backup_once(pack)
                    pack.write_text(json.dumps(cur, indent=4), encoding="utf-8")
                    log(f"Обновлены версии: Minecraft {m.minecraft}, {m.loader_type} {m.loader_version}")
            except (OSError, ValueError):
                log("⚠ mmc-pack.json не удалось прочитать, версии не менялись")
    return inst


def apply_instance_settings(inst: Path, java_path: str = "", mem_min: int = 0, mem_max: int = 0,
                            prelaunch_exe: Optional[str] = None, enable_prelaunch: bool = False) -> List[str]:
    """Пишет Java/память/автообновление в instance.cfg. Freesm при этом должен быть закрыт."""
    cfg = Path(inst) / "instance.cfg"
    upd: Dict[str, str] = {}
    done: List[str] = []
    if java_path:
        upd["OverrideJavaLocation"] = "true"
        upd["JavaPath"] = _posix(java_path)
        done.append(f"Java: {java_path}")
    if mem_max:
        upd["OverrideMemory"] = "true"
        upd["MinMemAlloc"] = str(mem_min or mem_max)
        upd["MaxMemAlloc"] = str(mem_max)
        done.append(f"Память: {mem_max} МБ")
    cur_pre = ini_get(cfg, "PreLaunchCommand") or ""
    if enable_prelaunch and prelaunch_exe:
        exe = _posix(prelaunch_exe)
        upd["OverrideCommands"] = "true"
        upd["PreLaunchCommand"] = f'"{exe}" {PRELAUNCH_MARK} "$INST_DIR"'
        done.append("Автообновление при запуске из Freesm: включено")
    elif not enable_prelaunch and PRELAUNCH_MARK in cur_pre:
        upd["PreLaunchCommand"] = ""
        upd["OverrideCommands"] = "false"
        done.append("Автообновление при запуске из Freesm: выключено")
    if upd:
        ini_set(cfg, upd)
    return done


# ---------- процессы, аккаунты, запуск ----------

def is_running(image_names=("FreesmLauncher.exe", "prismlauncher.exe")) -> bool:
    if not IS_WIN:
        return False
    for name in image_names:
        try:
            out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
                                 capture_output=True, text=True, timeout=8,
                                 creationflags=_NO_WINDOW).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if name.lower() in out.lower():
            return True
    return False


def account_names(root: Path) -> Optional[List[str]]:
    """Имена профилей из accounts.json Freesm (только чтение). None — файла нет или он нечитаем."""
    p = Path(root) / "accounts.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    names: List[str] = []

    def walk(o):
        if isinstance(o, dict):
            prof = o.get("profile")
            if isinstance(prof, dict) and isinstance(prof.get("name"), str):
                names.append(prof["name"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return names


def build_launch_args(exe: Path, root: Optional[Path], instance_id: str,
                      profile: str = "", server: str = "") -> List[str]:
    """root=None — Freesm установлен как обычная программа: -d не передаём, он сам знает свою папку данных."""
    args = [str(exe)] + (["-d", str(root)] if root else []) + ["-l", instance_id]
    if server:
        args += ["-s", server]
    if profile:
        args += ["-a", profile]
    return args


def open_app(exe: Path, root: Optional[Path]) -> None:
    """Открывает окно Freesm (например, чтобы добавить оффлайн-аккаунт)."""
    flags = (0x00000008 | 0x00000200) if IS_WIN else 0
    subprocess.Popen([str(exe)] + (["-d", str(root)] if root else []), cwd=str(Path(exe).parent), creationflags=flags,
                     close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def launch(exe: Path, root: Optional[Path], instance_id: str, profile: str = "", server: str = "") -> None:
    args = build_launch_args(exe, root, instance_id, profile, server)
    flags = (0x00000008 | 0x00000200) if IS_WIN else 0  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen(args, cwd=str(Path(exe).parent), creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
