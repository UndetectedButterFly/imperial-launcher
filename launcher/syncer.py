"""Движок синхронизации сборки.

Режимы файлов (поле mode в манифесте):
  enforce — файл на диске обязан совпадать с манифестом (моды). Лишнее удаляется, битое чинится.
  update  — перезаписывается ТОЛЬКО когда в манифесте появилась новая версия файла.
            Правки пользователя живут до следующего обновления; то, что мод сам
            переписывает при запуске (CustomSkinLoader.json), не вызывает перекачки.
  once    — ставится один раз, если файла нет. Дальше это пользовательский файл.
  ignore  — лаунчер не трогает.
Пути из protected (и DEFAULT_PROTECTED) не затрагиваются ни при каких условиях.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import net
from .manifest import (
    DEFAULT_PROTECTED,
    FileEntry,
    Manifest,
    ManifestError,
    matches_any,
    normalize_rel,
)
from .net import Cancelled

STATE_DIR = ".imperial"
STATE_FILE = "state.json"


@dataclass
class SyncOptions:
    quarantine_unknown: bool = False      # убирать посторонние .jar из mods/ в карантин
    extra_allowed: List[str] = field(default_factory=list)  # шаблоны разрешённых чужих модов
    workers: int = 4
    dry_run: bool = False


@dataclass
class Action:
    kind: str            # download | delete | quarantine | forget | adopt | keep
    path: str
    entry: Optional[FileEntry] = None
    reason: str = ""


@dataclass
class SyncResult:
    version: str = ""
    downloaded: int = 0
    replaced: int = 0
    deleted: int = 0
    kept_user_changes: int = 0
    unchanged: int = 0
    skipped_protected: int = 0
    quarantined: List[str] = field(default_factory=list)
    unknown_mods: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    bytes_total: int = 0
    plan: List[Action] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        parts = [
            f"скачано {self.downloaded}",
            f"заменено {self.replaced}",
            f"удалено {self.deleted}",
            f"без изменений {self.unchanged}",
        ]
        if self.kept_user_changes:
            parts.append(f"сохранены правки пользователя {self.kept_user_changes}")
        if self.quarantined:
            parts.append(f"в карантин {len(self.quarantined)}")
        if self.errors:
            parts.append(f"ОШИБОК {len(self.errors)}")
        return ", ".join(parts)


# ---------- состояние ----------

def state_path(game_dir: Path) -> Path:
    return Path(game_dir) / STATE_DIR / STATE_FILE


def load_state(game_dir: Path) -> dict:
    try:
        data = json.loads(state_path(game_dir).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("files"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"schema": 1, "pack_version": "", "files": {}}


def save_state(game_dir: Path, state: dict) -> None:
    p = state_path(game_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def installed_version(game_dir: Path) -> str:
    return str(load_state(game_dir).get("pack_version", ""))


# ---------- утилиты ----------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_dest(game_dir: Path, rel: str) -> Path:
    """Путь внутри game_dir; выход за пределы (через .. или симлинки) запрещён."""
    root = Path(game_dir).resolve()
    dest = (root / normalize_rel(rel)).resolve()
    if dest != root and root not in dest.parents:
        raise ManifestError(f"путь выходит за пределы папки игры: {rel}")
    return dest


def _prune_empty_dirs(start: Path, stop: Path) -> None:
    """Удаляет пустые подпапки глубже первого уровня (mods/ и config/ не трогаем)."""
    stop = stop.resolve()
    cur = start.resolve()
    while cur != stop and stop in cur.parents and cur.parent != stop:
        try:
            cur.rmdir()
        except OSError:
            break
        cur = cur.parent


# ---------- планирование ----------

def build_plan(manifest: Manifest, game_dir: Path, opts: SyncOptions,
               state: Optional[dict] = None,
               log: Callable[[str], None] = lambda s: None) -> SyncResult:
    game_dir = Path(game_dir)
    state = state if state is not None else load_state(game_dir)
    sfiles: Dict[str, dict] = state.get("files", {})
    protected = list(DEFAULT_PROTECTED) + list(manifest.protected)
    res = SyncResult(version=manifest.version)
    wanted: Dict[str, FileEntry] = {}

    def prot(p: str) -> bool:
        return matches_any(p, protected)

    for e in manifest.files:
        if e.mode == "ignore":
            continue
        if prot(e.path):
            res.skipped_protected += 1
            continue
        wanted[e.path] = e
        disk = safe_dest(game_dir, e.path)
        exists = disk.is_file()
        st = sfiles.get(e.path)

        if e.mode == "enforce":
            if not exists:
                res.plan.append(Action("download", e.path, e, "новый"))
            elif disk.stat().st_size != e.size or sha256_file(disk) != e.sha256:
                res.plan.append(Action("download", e.path, e, "заменить"))
            else:
                res.plan.append(Action("adopt", e.path, e))
        elif e.mode == "update":
            if not exists:
                res.plan.append(Action("download", e.path, e, "новый"))
            elif st is None:
                if disk.stat().st_size == e.size and sha256_file(disk) == e.sha256:
                    res.plan.append(Action("adopt", e.path, e))
                else:
                    res.plan.append(Action("download", e.path, e, "заменить"))
            elif st.get("sha256") != e.sha256:
                res.plan.append(Action("download", e.path, e, "новая версия"))
            else:
                res.plan.append(Action("adopt", e.path, e))
        else:  # once
            if exists or st is not None:
                res.plan.append(Action("adopt", e.path, e))
            else:
                res.plan.append(Action("download", e.path, e, "новый"))

    # то, что раньше ставил лаунчер, а в манифесте больше нет
    for path, st in list(sfiles.items()):
        if path in wanted:
            continue
        try:
            disk = safe_dest(game_dir, path)
        except ManifestError:
            res.plan.append(Action("forget", path, reason="некорректный путь в состоянии"))
            continue
        mode = st.get("mode", "once")
        if prot(path) or not disk.is_file() or mode == "once":
            res.plan.append(Action("forget", path))
        elif mode == "enforce":
            res.plan.append(Action("delete", path, reason="убрано из сборки"))
        elif sha256_file(disk) == st.get("sha256"):
            res.plan.append(Action("delete", path, reason="убрано из сборки"))
        else:
            res.kept_user_changes += 1
            res.plan.append(Action("forget", path, reason="изменён пользователем, оставлен"))

    # явный список удаляемых файлов (старые версии модов после ручной установки)
    deleting = {a.path.lower() for a in res.plan if a.kind == "delete"}
    for path in manifest.remove:
        if path in wanted or prot(path) or path.lower() in deleting:
            continue
        if safe_dest(game_dir, path).is_file():
            res.plan.append(Action("delete", path, reason="устаревший файл"))
            deleting.add(path.lower())

    # посторонние моды
    mods = game_dir / "mods"
    if mods.is_dir():
        known = {p.lower() for p in wanted if p.lower().startswith("mods/")}
        allowed = list(opts.extra_allowed) + list(manifest.allow_extra_mods)
        for f in sorted(mods.glob("*.jar")):
            rel = f"mods/{f.name}"
            if rel.lower() in known or rel.lower() in deleting:
                continue
            if allowed and matches_any(rel, allowed):
                continue
            if opts.quarantine_unknown:
                res.plan.append(Action("quarantine", rel, reason="не входит в сборку"))
            else:
                res.unknown_mods.append(rel)

    res.bytes_total = sum(a.entry.size for a in res.plan if a.kind == "download" and a.entry)
    return res


# ---------- применение ----------

def sync(manifest: Manifest, game_dir: Path, opts: Optional[SyncOptions] = None,
         log: Callable[[str], None] = lambda s: None,
         progress: Callable[[int, int, str], None] = lambda d, t, n: None,
         cancel: Optional[threading.Event] = None) -> SyncResult:
    opts = opts or SyncOptions()
    game_dir = Path(game_dir)
    game_dir.mkdir(parents=True, exist_ok=True)
    state = load_state(game_dir)
    log("Проверяю файлы…")
    res = build_plan(manifest, game_dir, opts, state, log)

    downloads = [a for a in res.plan if a.kind == "download"]
    log(f"План: скачать {len(downloads)} ({res.bytes_total / 1048576:.1f} МБ), "
        f"удалить {sum(1 for a in res.plan if a.kind == 'delete')}")
    if opts.dry_run:
        return res

    sfiles: Dict[str, dict] = state.setdefault("files", {})

    # 1) удаление и карантин
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for a in res.plan:
        try:
            if a.kind == "delete":
                p = safe_dest(game_dir, a.path)
                p.unlink()
                _prune_empty_dirs(p.parent, game_dir)
                sfiles.pop(a.path, None)
                res.deleted += 1
                log(f"− удалён {a.path} ({a.reason})")
            elif a.kind == "quarantine":
                src = safe_dest(game_dir, a.path)
                qdir = game_dir / STATE_DIR / "quarantine" / stamp
                qdir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(qdir / src.name))
                res.quarantined.append(a.path)
                log(f"⚠ в карантин: {a.path}")
            elif a.kind == "forget":
                sfiles.pop(a.path, None)
                if a.reason:
                    log(f"· {a.path}: {a.reason}")
        except PermissionError:
            res.errors.append(f"файл занят, закройте игру и Freesm: {a.path}")
        except OSError as e:
            res.errors.append(f"{a.path}: {e}")

    # 2) уже актуальные файлы — просто запоминаем как «наши»
    for a in res.plan:
        if a.kind == "adopt" and a.entry:
            sfiles[a.path] = {"sha256": a.entry.sha256, "mode": a.entry.mode}
            res.unchanged += 1

    # 3) загрузки
    total = res.bytes_total
    done = 0
    current = ""
    lock = threading.Lock()

    def on_bytes(n: int) -> None:
        nonlocal done
        with lock:
            done += n
            progress(max(done, 0), total, current)

    def work(a: Action) -> Action:
        nonlocal current
        current = a.path
        e = a.entry
        assert e is not None
        net.download_file(e.url, safe_dest(game_dir, e.path), e.sha256, e.size,
                          on_bytes=on_bytes, cancel=cancel)
        return a

    if downloads:
        with ThreadPoolExecutor(max_workers=max(1, opts.workers)) as pool:
            futures = {pool.submit(work, a): a for a in downloads}
            try:
                for fut in as_completed(futures):
                    a = futures[fut]
                    try:
                        fut.result()
                    except Cancelled:
                        raise
                    except PermissionError:
                        res.errors.append(f"файл занят, закройте игру и Freesm: {a.path}")
                        continue
                    except Exception as ex:  # сеть, хэш, диск
                        res.errors.append(f"{a.path}: {ex}")
                        log(f"✗ {a.path}: {ex}")
                        continue
                    e = a.entry
                    sfiles[a.path] = {"sha256": e.sha256, "mode": e.mode}
                    if a.reason == "новый":
                        res.downloaded += 1
                        log(f"+ {a.path}")
                    else:
                        res.replaced += 1
                        log(f"↻ {a.path} ({a.reason})")
            except Cancelled:
                if cancel is not None:
                    cancel.set()
                for f in futures:
                    f.cancel()
                save_state(game_dir, state)
                raise

    if res.ok:
        state["pack_version"] = manifest.version
    save_state(game_dir, state)
    if res.unknown_mods:
        log("⚠ Посторонние моды (не входят в сборку, могут вызвать конфликты): "
            + ", ".join(m.split("/", 1)[1] for m in res.unknown_mods))
    log("Готово: " + res.summary())
    return res
