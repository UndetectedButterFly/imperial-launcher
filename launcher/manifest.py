"""Манифест сборки: разбор, проверка путей и сопоставление glob-шаблонов."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import net

MODES = ("enforce", "update", "once", "ignore")
_SHA = re.compile(r"^[0-9a-f]{64}$")

# Эти пути лаунчер НЕ трогает никогда (пользовательский контент).
DEFAULT_PROTECTED = [
    "options.txt",
    "optionsof.txt",
    "optionsshaders.txt",
    "servers.dat",
    "servers.dat_old",
    "usercache.json",
    "usernamecache.json",
    "saves/**",
    "screenshots/**",
    "logs/**",
    "crash-reports/**",
    "journeymap/**",
    "xaero/**",
    "XaeroWorldMap/**",
    "XaeroWaypoints/**",
    "schematics/**",
    "backups/**",
    ".imperial/**",
]


class ManifestError(Exception):
    """Манифест повреждён или небезопасен."""


@dataclass(frozen=True)
class FileEntry:
    path: str
    sha256: str
    size: int
    url: str
    mode: str


@dataclass
class Manifest:
    version: str
    pack: str = "pack"
    name: str = ""
    minecraft: str = ""
    loader_type: str = ""
    loader_version: str = ""
    java_major: int = 21
    memory_min: int = 2048
    memory_max: int = 6144
    server_name: str = ""
    server_address: str = ""
    instance_id: str = "ImperialPack"
    min_launcher: str = "0"
    changelog: List[str] = field(default_factory=list)
    protected: List[str] = field(default_factory=list)
    allow_extra_mods: List[str] = field(default_factory=list)
    files: List[FileEntry] = field(default_factory=list)
    remove: List[str] = field(default_factory=list)


def normalize_rel(path: str) -> str:
    """Приводит путь к виду 'a/b/c' и отвергает всё, что выходит за пределы папки игры."""
    if not isinstance(path, str) or not path:
        raise ManifestError("пустой путь в манифесте")
    p = path.replace("\\", "/")
    if p.startswith("/") or re.match(r"^[A-Za-z]:", p):
        raise ManifestError(f"абсолютный путь запрещён: {path}")
    parts = p.split("/")
    for seg in parts:
        if seg in ("", ".", ".."):
            raise ManifestError(f"недопустимый путь: {path}")
        if any(c in seg for c in '<>:"|?*') or any(ord(c) < 32 for c in seg):
            raise ManifestError(f"недопустимые символы в пути: {path}")
        if seg.endswith((" ", ".")):
            raise ManifestError(f"недопустимое имя: {path}")
    if parts[0].lower() == ".imperial":
        raise ManifestError(f"служебная папка запрещена: {path}")
    return "/".join(parts)


@lru_cache(maxsize=512)
def _glob_regex(pattern: str) -> "re.Pattern[str]":
    p = pattern.replace("\\", "/")
    i, out = 0, []
    while i < len(p):
        c = p[i]
        if p.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif p.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE)


def matches_any(path: str, patterns) -> bool:
    return any(_glob_regex(p).match(path) for p in patterns)


def version_tuple(v: str) -> Tuple[int, ...]:
    nums = re.findall(r"\d+", str(v))
    return tuple(int(n) for n in nums) or (0,)


def _req(d: dict, key: str, typ):
    if key not in d or not isinstance(d[key], typ):
        raise ManifestError(f"в манифесте нет поля «{key}» или оно неверного типа")
    return d[key]


def parse_manifest(data: dict, allow_file_urls: bool = False) -> Manifest:
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise ManifestError("неподдерживаемая версия манифеста (нужна schema=1)")
    m = Manifest(version=str(_req(data, "version", str)))
    m.pack = str(data.get("pack", "pack"))
    m.name = str(data.get("name", m.pack))
    m.minecraft = str(data.get("minecraft", ""))
    loader = data.get("loader") or {}
    m.loader_type = str(loader.get("type", ""))
    m.loader_version = str(loader.get("version", ""))
    m.java_major = int(data.get("java_major", 21))
    mem = data.get("memory") or {}
    m.memory_min = int(mem.get("min", 2048))
    m.memory_max = int(mem.get("max", 6144))
    srv = data.get("server") or {}
    m.server_name = str(srv.get("name", ""))
    m.server_address = str(srv.get("address", ""))
    m.instance_id = str(data.get("instance_id", "ImperialPack"))
    m.min_launcher = str(data.get("min_launcher", "0"))
    m.changelog = [str(x) for x in data.get("changelog", [])][:300]
    m.protected = [str(x) for x in data.get("protected", [])]
    m.allow_extra_mods = [str(x) for x in data.get("allow_extra_mods", [])]
    if re.search(r'[\\/:*?"<>|]', m.instance_id) or m.instance_id in ("", ".", ".."):
        raise ManifestError("недопустимый instance_id")
    seen = set()
    for raw in _req(data, "files", list):
        if not isinstance(raw, dict):
            raise ManifestError("запись файла должна быть объектом")
        path = normalize_rel(raw.get("path"))
        sha = str(raw.get("sha256", "")).lower()
        url = str(raw.get("url", ""))
        mode = str(raw.get("mode", "once"))
        size = raw.get("size")
        if not _SHA.match(sha):
            raise ManifestError(f"неверный sha256 у {path}")
        if not isinstance(size, int) or size < 0:
            raise ManifestError(f"неверный размер у {path}")
        if mode not in MODES:
            raise ManifestError(f"неизвестный режим «{mode}» у {path}")
        schemes = ("https://", "http://") + (("file://",) if allow_file_urls else ())
        if mode != "ignore" and not url.lower().startswith(schemes):
            raise ManifestError(f"недопустимый адрес у {path}")
        key = path.lower()
        if key in seen:
            raise ManifestError(f"повтор пути: {path}")
        seen.add(key)
        m.files.append(FileEntry(path, sha, size, url, mode))
    m.remove = [normalize_rel(x) for x in data.get("remove", [])]
    return m


def load_manifest(source: str, allow_file_urls: bool = False) -> Manifest:
    """source — URL (http/https) или путь к локальному файлу."""
    if source.lower().startswith(("http://", "https://")):
        data = net.get_json(source)
    else:
        data = json.loads(Path(source).read_text(encoding="utf-8"))
    return parse_manifest(data, allow_file_urls=allow_file_urls)
