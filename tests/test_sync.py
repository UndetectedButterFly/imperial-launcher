"""Тесты движка синхронизации. Запуск: python -m unittest discover -s tests -v"""
import functools
import hashlib
import http.server
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from launcher import net  # noqa: E402
from launcher.manifest import ManifestError, parse_manifest, matches_any  # noqa: E402
from launcher.syncer import SyncOptions, load_state, sync  # noqa: E402

net.RETRY_DELAY = 0


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class Env:
    """Фейковый «релиз»: папка с файлами + HTTP-сервер + игровая папка."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.srv_dir = root / "srv"
        self.srv_dir.mkdir()
        self.game = root / "game"
        handler = functools.partial(Quiet, directory=str(self.srv_dir))
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.files = []

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def add(self, rel, content: bytes, mode, corrupt=False):
        sha = hashlib.sha256(content).hexdigest()
        (self.srv_dir / sha).write_bytes(b"BROKEN!!" if corrupt else content)
        self.files.append(
            {"path": rel, "sha256": sha, "size": len(content),
             "url": f"http://127.0.0.1:{self.port}/{sha}", "mode": mode})

    def manifest(self, version, **extra):
        data = {"schema": 1, "version": version, "files": list(self.files)}
        data.update(extra)
        return parse_manifest(data)

    def reset(self):
        self.files = []


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.g = self.env.game

    def tearDown(self):
        self.env.close()

    def run_sync(self, version, **kw):
        extra = kw.pop("manifest_extra", {})
        m = self.env.manifest(version, **extra)
        return sync(m, self.g, SyncOptions(workers=3, **kw))

    def test_fresh_install_and_idempotent(self):
        e = self.env
        e.add("mods/a.jar", b"A" * 1000, "enforce")
        e.add("config/x.toml", b"x=1", "once")
        e.add("CustomSkinLoader/CustomSkinLoader.json", b"{}", "update")
        r = self.run_sync("1.0")
        self.assertTrue(r.ok, r.errors)
        self.assertEqual(r.downloaded, 3)
        self.assertEqual((self.g / "mods/a.jar").read_bytes(), b"A" * 1000)
        self.assertEqual(load_state(self.g)["pack_version"], "1.0")
        r2 = self.run_sync("1.0")
        self.assertEqual((r2.downloaded, r2.replaced, r2.deleted), (0, 0, 0))
        self.assertEqual(r2.unchanged, 3)

    def test_once_keeps_user_changes_and_deletions(self):
        e = self.env
        e.add("config/x.toml", b"x=1", "once")
        self.run_sync("1.0")
        (self.g / "config/x.toml").write_text("x=USER")
        e.reset()
        e.add("config/x.toml", b"x=2", "once")  # в сборке файл обновили
        self.run_sync("1.1")
        self.assertEqual((self.g / "config/x.toml").read_text(), "x=USER")
        (self.g / "config/x.toml").unlink()      # пользователь удалил — не возвращаем
        self.run_sync("1.1")
        self.assertFalse((self.g / "config/x.toml").exists())

    def test_update_mode_only_on_new_manifest_hash(self):
        e = self.env
        e.add("CSL.json", b"v1", "update")
        self.run_sync("1.0")
        (self.g / "CSL.json").write_text("mod-rewrote-it")  # мод переписал файл сам
        r = self.run_sync("1.0")
        self.assertEqual(r.replaced, 0)
        self.assertEqual((self.g / "CSL.json").read_text(), "mod-rewrote-it")
        e.reset()
        e.add("CSL.json", b"v2", "update")                  # вышла новая версия
        r = self.run_sync("1.1")
        self.assertEqual(r.replaced, 1)
        self.assertEqual((self.g / "CSL.json").read_bytes(), b"v2")

    def test_enforce_repairs_tampered_jar(self):
        e = self.env
        e.add("mods/a.jar", b"A" * 500, "enforce")
        self.run_sync("1.0")
        (self.g / "mods/a.jar").write_bytes(b"B" * 500)
        r = self.run_sync("1.0")
        self.assertEqual(r.replaced, 1)
        self.assertEqual((self.g / "mods/a.jar").read_bytes(), b"A" * 500)

    def test_removed_from_pack_is_deleted_user_mod_survives(self):
        e = self.env
        e.add("mods/a.jar", b"A", "enforce")
        e.add("mods/b.jar", b"B", "enforce")
        self.run_sync("1.0")
        (self.g / "mods/mine.jar").write_bytes(b"MINE")
        e.reset()
        e.add("mods/b.jar", b"B", "enforce")
        e.add("mods/c.jar", b"C", "enforce")
        r = self.run_sync("1.1")
        self.assertFalse((self.g / "mods/a.jar").exists())
        self.assertTrue((self.g / "mods/c.jar").exists())
        self.assertTrue((self.g / "mods/mine.jar").exists())
        self.assertEqual(r.unknown_mods, ["mods/mine.jar"])
        self.assertEqual(r.deleted, 1)

    def test_quarantine_unknown(self):
        e = self.env
        e.add("mods/a.jar", b"A", "enforce")
        (self.g / "mods").mkdir(parents=True)
        (self.g / "mods/old-version.jar").write_bytes(b"OLD")
        (self.g / "mods/friend.jar").write_bytes(b"F")
        r = self.run_sync("1.0", quarantine_unknown=True, extra_allowed=["mods/friend.jar"])
        self.assertEqual(r.quarantined, ["mods/old-version.jar"])
        self.assertFalse((self.g / "mods/old-version.jar").exists())
        self.assertTrue((self.g / "mods/friend.jar").exists())
        moved = list((self.g / ".imperial/quarantine").rglob("old-version.jar"))
        self.assertEqual(len(moved), 1)

    def test_protected_never_touched(self):
        e = self.env
        e.add("options.txt", b"evil", "enforce")
        e.add("saves/world/level.dat", b"evil", "enforce")
        self.g.mkdir(parents=True)
        (self.g / "options.txt").write_text("key_key.attack:key.mouse.left")
        r = self.run_sync("1.0")
        self.assertEqual(r.skipped_protected, 2)
        self.assertEqual((self.g / "options.txt").read_text(), "key_key.attack:key.mouse.left")
        self.assertFalse((self.g / "saves").exists())

    def test_protected_from_manifest_blocks_removal(self):
        e = self.env
        e.add("config/keep.toml", b"1", "update")
        self.run_sync("1.0")
        e.reset()
        r = self.run_sync("1.1", manifest_extra={"protected": ["config/keep.toml"]})
        self.assertTrue((self.g / "config/keep.toml").exists())
        self.assertEqual(r.deleted, 0)

    def test_modified_update_file_not_deleted_when_removed(self):
        e = self.env
        e.add("config/u.toml", b"orig", "update")
        self.run_sync("1.0")
        (self.g / "config/u.toml").write_text("user-edit")
        e.reset()
        r = self.run_sync("1.1")
        self.assertEqual((self.g / "config/u.toml").read_text(), "user-edit")
        self.assertEqual(r.kept_user_changes, 1)

    def test_explicit_remove_list(self):
        e = self.env
        e.add("mods/new.jar", b"N", "enforce")
        (self.g / "mods").mkdir(parents=True)
        (self.g / "mods/old.jar").write_bytes(b"O")
        r = self.run_sync("1.0", manifest_extra={"remove": ["mods/old.jar"]})
        self.assertFalse((self.g / "mods/old.jar").exists())
        self.assertEqual(r.deleted, 1)

    def test_corrupt_download_reports_error_and_keeps_version(self):
        e = self.env
        e.add("mods/ok.jar", b"OK", "enforce")
        e.add("mods/bad.jar", b"GOODBYTES", "enforce", corrupt=True)
        r = self.run_sync("2.0")
        self.assertFalse(r.ok)
        self.assertTrue((self.g / "mods/ok.jar").exists())
        self.assertFalse((self.g / "mods/bad.jar").exists())
        self.assertNotEqual(load_state(self.g)["pack_version"], "2.0")
        self.assertEqual(list(self.g.rglob("*.imperial-part")), [])

    def test_dry_run_changes_nothing(self):
        e = self.env
        e.add("mods/a.jar", b"A", "enforce")
        m = e.manifest("1.0")
        r = sync(m, self.g, SyncOptions(dry_run=True))
        self.assertEqual(len([a for a in r.plan if a.kind == "download"]), 1)
        self.assertFalse((self.g / "mods/a.jar").exists())

    def test_bad_paths_rejected(self):
        for bad in ["../evil.txt", "/abs.txt", "C:/win.txt", "a/../../b", "mods\\..\\x", ".imperial/state.json"]:
            data = {"schema": 1, "version": "1", "files": [
                {"path": bad, "sha256": "0" * 64, "size": 1, "url": "https://x/y", "mode": "once"}]}
            with self.assertRaises(ManifestError, msg=bad):
                parse_manifest(data)

    def test_bad_url_and_mode_rejected(self):
        base = {"path": "a.txt", "sha256": "0" * 64, "size": 1}
        with self.assertRaises(ManifestError):
            parse_manifest({"schema": 1, "version": "1", "files": [dict(base, url="ftp://x", mode="once")]})
        with self.assertRaises(ManifestError):
            parse_manifest({"schema": 1, "version": "1", "files": [dict(base, url="https://x", mode="weird")]})

    def test_glob(self):
        self.assertTrue(matches_any("saves/a/b.dat", ["saves/**"]))
        self.assertTrue(matches_any("mods/Create.JAR", ["mods/*.jar"]))
        self.assertFalse(matches_any("mods/sub/x.jar", ["mods/*.jar"]))
        self.assertTrue(matches_any("a/b/c.toml", ["**/c.toml"]))
        self.assertTrue(matches_any("c.toml", ["**/c.toml"]))
        self.assertFalse(matches_any("options.txt.bak", ["options.txt"]))


if __name__ == "__main__":
    unittest.main()
