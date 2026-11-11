"""Тесты Freesm-части и сквозного сценария «установка -> обновление»."""
import functools
import hashlib
import http.server
import io
import json
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from launcher import core, freesm, javafind, net, others  # noqa: E402
from launcher.manifest import parse_manifest  # noqa: E402
from launcher.settings import Settings  # noqa: E402

net.RETRY_DELAY = 0


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class FreesmHelpers(unittest.TestCase):
    def test_ini_set_preserves_and_updates(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "instance.cfg"
            p.write_text("[General]\r\nConfigVersion=1.2\r\nname=Test\r\nJavaPath=old\r\n", encoding="utf-8")
            freesm.ini_set(p, {"JavaPath": "C:/Java/bin/javaw.exe", "OverrideJavaLocation": "true"})
            text = p.read_text(encoding="utf-8")
            self.assertIn("JavaPath=C:/Java/bin/javaw.exe", text)
            self.assertIn("OverrideJavaLocation=true", text)
            self.assertIn("name=Test", text)
            self.assertNotIn("JavaPath=old", text)
            self.assertIn(b"\r\n", p.read_bytes())  # перевод строк сохранён (read_text его скрывает)
            self.assertTrue((Path(d) / "instance.cfg.imperial-bak").exists())
            self.assertEqual(freesm.ini_get(p, "name"), "Test")

    def test_ini_set_without_header(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.cfg"
            p.write_text("a=1\n", encoding="utf-8")
            freesm.ini_set(p, {"b": "2"})
            self.assertTrue(p.read_text().startswith("[General]"))
            self.assertEqual(freesm.ini_get(p, "b"), "2")

    def test_pick_asset(self):
        rel = {"assets": [
            {"name": "FreesmLauncher-Windows-MSVC-Setup-2.2.2.exe"},
            {"name": "FreesmLauncher-Windows-MSVC-arm64-Portable-2.2.2.zip"},
            {"name": "FreesmLauncher-Windows-MSVC-Portable-2.2.2.zip", "browser_download_url": "u"},
            {"name": "FreesmLauncher-Linux-Portable-2.2.2.tar.gz"}]}
        self.assertEqual(freesm.pick_windows_asset(rel)["name"], "FreesmLauncher-Windows-MSVC-Portable-2.2.2.zip")
        with self.assertRaises(freesm.FreesmError):
            freesm.pick_windows_asset({"assets": [{"name": "FreesmLauncher-Windows-MSVC-Setup-2.2.2.exe"}]})

    def test_zip_slip_blocked(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("../evil.txt", "x")
        buf.seek(0)
        with tempfile.TemporaryDirectory() as d, zipfile.ZipFile(buf) as z:
            with self.assertRaises(freesm.FreesmError):
                freesm._safe_extract(z, Path(d) / "out")

    def test_accounts_and_launch_args(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(freesm.account_names(Path(d)))
            (Path(d) / "accounts.json").write_text(json.dumps(
                {"accounts": [{"type": "Offline", "profile": {"name": "Jeff", "id": "1"}}], "formatVersion": 3}))
            self.assertEqual(freesm.account_names(Path(d)), ["Jeff"])
        args = freesm.build_launch_args(Path("F.exe"), Path("root"), "Imp", "Jeff", "1.2.3.4:25565")
        self.assertEqual(args, ["F.exe", "-d", "root", "-l", "Imp", "-s", "1.2.3.4:25565", "-a", "Jeff"])

    def test_prelaunch_toggle(self):
        with tempfile.TemporaryDirectory() as d:
            inst = Path(d)
            (inst / "instance.cfg").write_text("[General]\nname=x\n")
            freesm.apply_instance_settings(inst, enable_prelaunch=True, prelaunch_exe="C:\\Games\\IL.exe")
            pre = freesm.ini_get(inst / "instance.cfg", "PreLaunchCommand")
            self.assertEqual(pre, '"C:/Games/IL.exe" --sync "$INST_DIR"')
            self.assertEqual(freesm.ini_get(inst / "instance.cfg", "OverrideCommands"), "true")
            freesm.apply_instance_settings(inst, enable_prelaunch=False)
            self.assertEqual(freesm.ini_get(inst / "instance.cfg", "PreLaunchCommand"), "")
            self.assertEqual(freesm.ini_get(inst / "instance.cfg", "OverrideCommands"), "false")

    def test_parse_java_version(self):
        self.assertEqual(javafind.parse_major('openjdk version "21.0.7" 2025-04-15 LTS'), (21, "21.0.7"))
        self.assertEqual(javafind.parse_major('java version "1.8.0_392"'), (8, "1.8.0_392"))
        self.assertIsNone(javafind.parse_major("garbage"))
        js = [javafind.JavaInfo("a", 17, "17"), javafind.JavaInfo("b", 21, "21"), javafind.JavaInfo("c", 25, "25")]
        self.assertEqual(javafind.best_for(js, 21).path, "b")
        self.assertEqual(javafind.best_for(js[:1] + js[2:], 21).path, "c")
        self.assertIsNone(javafind.best_for(js[:1], 21))


class OtherLaunchers(unittest.TestCase):
    def test_modrinth_profiles_listing_and_safety(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            for name in ("A", "B"):
                (Path(d) / "ModrinthApp" / "profiles" / name).mkdir(parents=True)
            (Path(d) / "ModrinthApp" / "profiles" / "file.txt").write_text("x")
            saved = {k: os.environ.get(k) for k in ("APPDATA", "XDG_DATA_HOME")}
            os.environ["APPDATA"] = os.environ["XDG_DATA_HOME"] = d
            try:
                if sys.platform == "darwin":
                    self.skipTest("macOS: другой путь")
                self.assertEqual([p.name for p in others.modrinth_profiles()], ["A", "B"])
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            self.assertNotEqual(others.unsafe_folder(Path.home()), "")
            self.assertNotEqual(others.unsafe_folder(Path(Path.home().anchor)), "")
            self.assertEqual(others.unsafe_folder(Path(d) / "ModrinthApp" / "profiles" / "A"), "")


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.srv = root / "srv"
        self.srv.mkdir()
        handler = functools.partial(Quiet, directory=str(self.srv))
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.port}"
        # «релиз Freesm» с поддельным exe
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("FreesmLauncher.exe", b"MZ-fake")
        (self.srv / "freesm.zip").write_bytes(buf.getvalue())
        self._orig = freesm.latest_release
        freesm.latest_release = lambda: {"assets": [{
            "name": "FreesmLauncher-Windows-MSVC-Portable-9.9.zip",
            "browser_download_url": f"{self.base}/freesm.zip", "size": len(buf.getvalue())}]}
        self.settings = Settings(root_dir=str(root / "ImperialMC"), nickname="Jeff")

    def tearDown(self):
        freesm.latest_release = self._orig
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def manifest(self, version, files):
        entries = []
        for rel, content, mode in files:
            sha = hashlib.sha256(content).hexdigest()
            (self.srv / sha).write_bytes(content)
            entries.append({"path": rel, "sha256": sha, "size": len(content),
                            "url": f"{self.base}/{sha}", "mode": mode})
        return parse_manifest({
            "schema": 1, "version": version, "name": "Империя", "instance_id": "Imp",
            "minecraft": "1.21.1", "loader": {"type": "neoforge", "version": "21.1.248"},
            "server": {"address": "1.2.3.4:25565"}, "files": entries, "changelog": ["+ mod"]})

    def test_install_then_update(self):
        logs = []
        m1 = self.manifest("1.0", [("mods/a.jar", b"AAA", "enforce"), ("config/c.toml", b"c=1", "once")])
        res = core.run_update(self.settings, logs.append, lambda *a: None, manifest=m1)
        self.assertTrue(res.ok, res.errors)
        root = Path(self.settings.root_dir) / "Freesm"
        self.assertTrue((root / "FreesmLauncher.exe").exists())
        self.assertTrue((root / "portable.txt").exists())
        inst = root / "instances" / "Imp"
        self.assertTrue((inst / "instance.cfg").exists())
        pack = json.loads((inst / "mmc-pack.json").read_text())
        vers = {c["uid"]: c["version"] for c in pack["components"]}
        self.assertEqual(vers["net.minecraft"], "1.21.1")
        self.assertEqual(vers["net.neoforged"], "21.1.248")
        self.assertEqual((inst / ".minecraft/mods/a.jar").read_bytes(), b"AAA")

        # пользователь меняет настройку мода и ставит свой мод; затем выходит обновление сборки
        (inst / ".minecraft/config/c.toml").write_text("c=USER")
        (inst / ".minecraft/options.txt").write_text("keys")
        m2 = self.manifest("1.1", [("mods/a2.jar", b"AAA2", "enforce"), ("config/c.toml", b"c=2", "once")])
        res = core.run_update(self.settings, logs.append, lambda *a: None, manifest=m2)
        self.assertTrue(res.ok, res.errors)
        self.assertFalse((inst / ".minecraft/mods/a.jar").exists())
        self.assertTrue((inst / ".minecraft/mods/a2.jar").exists())
        self.assertEqual((inst / ".minecraft/config/c.toml").read_text(), "c=USER")
        self.assertEqual((inst / ".minecraft/options.txt").read_text(), "keys")

    def test_play_does_not_block_on_unverified_account(self):
        m = self.manifest("1.0", [("mods/a.jar", b"AAA", "enforce")])
        core.run_update(self.settings, lambda s: None, lambda *a: None, manifest=m)
        launched, logs = [], []
        orig = freesm.launch
        freesm.launch = lambda exe, root, iid, profile, server: launched.append((iid, profile, server))
        try:
            core.run_update(self.settings, logs.append, lambda *a: None, play=True, manifest=m)
        finally:
            freesm.launch = orig
        self.assertEqual(launched, [("Imp", "Jeff", "1.2.3.4:25565")])
        self.assertTrue(any("Не удалось подтвердить аккаунт" in l for l in logs))

    def test_existing_instance_is_adopted_and_memory_untouched(self):
        root = Path(self.settings.root_dir).parent / "MyFreesm"
        (root / "instances/Mine/.minecraft/mods").mkdir(parents=True)
        (root / "FreesmLauncher.exe").write_bytes(b"MZ")
        (root / "portable.txt").write_text("")
        cfg = root / "instances/Mine/instance.cfg"
        cfg.write_text("[General]\nname=Моя сборка\nOverrideMemory=true\nMaxMemAlloc=9000\nMinMemAlloc=1000\n",
                       encoding="utf-8")
        (root / "instances/Mine/.minecraft/options.txt").write_text("my keys")
        (root / "instances/Mine/.minecraft/mods/a.jar").write_bytes(b"AAA")  # уже есть и совпадает
        self.settings.freesm_dir = str(root)
        self.settings.instance_id = "Mine"
        m = self.manifest("1.0", [("mods/a.jar", b"AAA", "enforce"), ("mods/b.jar", b"BBB", "enforce")])
        res = core.run_update(self.settings, lambda s: None, lambda *a: None, manifest=m)
        self.assertTrue(res.ok, res.errors)
        self.assertEqual(res.downloaded, 1)       # докачан только b.jar
        self.assertEqual(res.unchanged, 1)
        self.assertEqual(freesm.ini_get(cfg, "MaxMemAlloc"), "9000")   # память игрока не тронута
        self.assertEqual(freesm.ini_get(cfg, "name"), "Моя сборка")
        self.assertEqual((root / "instances/Mine/.minecraft/options.txt").read_text(), "my keys")
        self.assertEqual(freesm.list_instances(root), ["Mine"])
        # а если игрок сам включил управление памятью — применяем
        self.settings.manage_memory = True
        self.settings.memory_mb = 7168
        core.run_update(self.settings, lambda s: None, lambda *a: None, manifest=m)
        self.assertEqual(freesm.ini_get(cfg, "MaxMemAlloc"), "7168")

    def test_new_instance_gets_recommended_memory(self):
        m = self.manifest("1.0", [("mods/a.jar", b"AAA", "enforce")])
        core.run_update(self.settings, lambda s: None, lambda *a: None, manifest=m)
        cfg = Path(self.settings.root_dir) / "Freesm/instances/Imp/instance.cfg"
        self.assertEqual(freesm.ini_get(cfg, "MaxMemAlloc"), "6144")

    def test_non_portable_freesm_with_explicit_instances_dir(self):
        base = Path(self.settings.root_dir).parent
        prog = base / "ProgramFilesLike"
        prog.mkdir(parents=True)
        (prog / "FreesmLauncher.exe").write_bytes(b"MZ")       # ни portable.txt, ни instances рядом
        inst_dir = base / "UserData" / "instances"
        (inst_dir / "Mine/.minecraft/mods").mkdir(parents=True)
        (inst_dir / "Mine/instance.cfg").write_text("[General]\nname=Моя\nMaxMemAlloc=9000\n", encoding="utf-8")
        (inst_dir / "Mine/.minecraft/options.txt").write_text("keys")
        self.settings.freesm_exe = str(prog / "FreesmLauncher.exe")
        self.settings.instances_dir = str(inst_dir)
        self.settings.instance_id = "Mine"
        paths = core.resolve_paths(self.settings)
        self.assertFalse(paths.portable)
        self.assertIsNone(paths.data_dir)
        m = self.manifest("1.0", [("mods/a.jar", b"AAA", "enforce")])
        launched = []
        orig = freesm.launch
        freesm.launch = lambda exe, root, iid, profile, server: launched.append((str(exe), root, iid))
        try:
            res = core.run_update(self.settings, lambda s: None, lambda *a: None, play=True, manifest=m)
        finally:
            freesm.launch = orig
        self.assertTrue(res.ok, res.errors)
        self.assertEqual((inst_dir / "Mine/.minecraft/mods/a.jar").read_bytes(), b"AAA")
        self.assertEqual((inst_dir / "Mine/.minecraft/options.txt").read_text(), "keys")
        self.assertEqual(freesm.ini_get(inst_dir / "Mine/instance.cfg", "MaxMemAlloc"), "9000")
        self.assertEqual(launched, [(str(prog / "FreesmLauncher.exe"), None, "Mine")])   # без -d
        self.assertEqual(sorted(p.name for p in prog.iterdir()), ["FreesmLauncher.exe"])  # exe-папку не трогали
        args = freesm.build_launch_args(prog / "FreesmLauncher.exe", None, "Mine", "J", "h:1")
        self.assertNotIn("-d", args)

    def test_non_portable_without_instances_dir_asks_user(self):
        base = Path(self.settings.root_dir).parent
        prog = base / "Prog2"
        prog.mkdir(parents=True)
        (prog / "FreesmLauncher.exe").write_bytes(b"MZ")
        self.settings.freesm_exe = str(prog / "FreesmLauncher.exe")
        m = self.manifest("1.0", [("mods/a.jar", b"AAA", "enforce")])
        with self.assertRaises(core.CoreError) as cm:
            core.run_update(self.settings, lambda s: None, lambda *a: None, manifest=m)
        self.assertIn("instances", str(cm.exception))
        self.assertEqual([p.name for p in prog.iterdir()], ["FreesmLauncher.exe"])  # ничего не создано

    def test_missing_exe_path_is_reported_not_reinstalled(self):
        self.settings.freesm_exe = str(Path(self.tmp.name) / "nope" / "FreesmLauncher.exe")
        m = self.manifest("1.0", [("mods/a.jar", b"AAA", "enforce")])
        with self.assertRaises(core.CoreError):
            core.run_update(self.settings, lambda s: None, lambda *a: None, manifest=m)

    def test_folder_mode_updates_modrinth_profile_without_freesm(self):
        prof = Path(self.tmp.name) / "ModrinthApp" / "profiles" / "Imperial"
        (prof / "mods").mkdir(parents=True)
        (prof / "options.txt").write_text("keys")
        (prof / "mods" / "mine.jar").write_bytes(b"MINE")
        self.settings.play_kind = "folder"
        self.settings.folder_dir = str(prof)
        m = self.manifest("1.0", [("mods/a.jar", b"AAA", "enforce"), ("config/c.toml", b"c", "once")])
        launched = []
        orig = freesm.launch
        freesm.launch = lambda *a: launched.append(a)
        try:
            res = core.run_update(self.settings, lambda s: None, lambda *a: None, play=True, manifest=m)
        finally:
            freesm.launch = orig
        self.assertTrue(res.ok, res.errors)
        self.assertEqual((prof / "mods/a.jar").read_bytes(), b"AAA")
        self.assertEqual((prof / "options.txt").read_text(), "keys")
        self.assertTrue((prof / "mods/mine.jar").exists())
        self.assertEqual(res.unknown_mods, ["mods/mine.jar"])
        self.assertEqual(launched, [])                                        # игру не запускаем
        self.assertFalse((Path(self.settings.root_dir) / "Freesm").exists())  # Freesm не ставим
        st = core.status(self.settings, m)
        self.assertEqual(st["installed_version"], "1.0")
        res2 = core.run_update(self.settings, lambda s: None, lambda *a: None, manifest=m)
        self.assertEqual((res2.downloaded, res2.replaced), (0, 0))

    def test_folder_mode_rejects_bad_folders(self):
        self.settings.play_kind = "folder"
        m = self.manifest("1.0", [("mods/a.jar", b"AAA", "enforce")])
        for bad in ("", str(Path.home()), str(Path(self.tmp.name) / "no-such-dir"), Path.home().anchor):
            self.settings.folder_dir = bad
            with self.assertRaises(core.CoreError, msg=bad):
                core.run_update(self.settings, lambda s: None, lambda *a: None, manifest=m)

    def test_prism_is_recognized_like_freesm(self):
        base = Path(self.tmp.name) / "PrismPortable"
        (base / "instances").mkdir(parents=True)
        (base / "prismlauncher.exe").write_bytes(b"MZ")
        self.assertEqual(freesm.find_freesm_exe(base).name, "prismlauncher.exe")
        self.settings.freesm_exe = str(base / "prismlauncher.exe")
        paths = core.resolve_paths(self.settings)
        self.assertTrue(paths.portable)
        self.assertEqual(paths.exe.name, "prismlauncher.exe")
        self.assertEqual(paths.instances_dir, base / "instances")
        args = freesm.build_launch_args(paths.exe, paths.data_dir, "Imp", "Jeff", "h:1")
        self.assertEqual(args[1:3], ["-d", str(base)])

    def test_headless_sync_never_raises(self):
        self.settings.pack_repo = "invalid/does-not-exist"
        self.assertFalse(core.run_headless_sync(self.settings, self.tmp.name, lambda s: None))


if __name__ == "__main__":
    unittest.main()
