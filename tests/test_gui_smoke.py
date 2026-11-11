"""Дымовой тест окна без настоящего tkinter: подставляем заглушки и прогоняем логику.
Ловит опечатки, неверные имена и ошибки в обработчиках; внешний вид он не проверяет."""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class Var:
    def __init__(self, master=None, value=None):
        self._v = value

    def get(self):
        return self._v

    def set(self, v):
        self._v = v


class FakeCTk:
    def __init__(self, *a, **k):
        self._after = []

    def after(self, ms, fn=None, *a):
        self._after.append(fn)

    def __getattr__(self, name):
        return MagicMock()


def install_fakes():
    tk = types.ModuleType("tkinter")
    tk.Canvas = lambda *a, **k: MagicMock()
    tk.StringVar = Var
    tk.IntVar = Var
    tk.BooleanVar = Var
    dialogs = types.ModuleType("tkinter.filedialog")
    dialogs.askdirectory = MagicMock(return_value="C:/Games/X")
    dialogs.askopenfilename = MagicMock(return_value="C:/Java/bin/javaw.exe")
    msg = types.ModuleType("tkinter.messagebox")
    msg.showinfo = MagicMock()
    msg.showwarning = MagicMock()
    msg.showerror = MagicMock()
    tk.filedialog, tk.messagebox = dialogs, msg
    ctk = types.ModuleType("customtkinter")
    ctk.CTk = FakeCTk
    ctk.set_appearance_mode = MagicMock()
    for name in ("CTkFrame", "CTkLabel", "CTkButton", "CTkEntry", "CTkSwitch", "CTkSlider",
                 "CTkOptionMenu", "CTkScrollableFrame", "CTkSegmentedButton", "CTkTextbox", "CTkProgressBar", "CTkFont"):
        setattr(ctk, name, MagicMock())
    sys.modules.update({"tkinter": tk, "tkinter.filedialog": dialogs,
                        "tkinter.messagebox": msg, "customtkinter": ctk})
    return msg


class GuiSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.msg = install_fakes()
        sys.modules.pop("launcher.gui", None)
        from launcher import gui
        cls.gui = gui

    def make_app(self):
        # не даём фоновым потокам ходить в сеть
        import threading
        orig = threading.Thread.start
        threading.Thread.start = lambda self: None
        try:
            app = self.gui.App()
        finally:
            threading.Thread.start = orig
        return app

    def manifest(self, version="1.1"):
        from launcher.manifest import parse_manifest
        return parse_manifest({"schema": 1, "version": version, "name": "Империя", "minecraft": "1.21.1",
                               "loader": {"type": "neoforge", "version": "21.1.248"},
                               "server": {"name": "The Sacred IT Empire", "address": "1.2.3.4:25565"},
                               "changelog": ["+ a", "- b"], "files": []})

    def test_construct_and_all_messages(self):
        app = self.make_app()
        app._handle(("no_repo", None))
        app._handle(("manifest_err", "нет сети"))
        app._handle(("manifest", self.manifest()))
        self.assertTrue(app.first_install)  # на чистой машине сборки нет
        app._handle(("progress", 5 * 1048576, 10 * 1048576, "mods/a.jar"))
        app._handle(("log", "строка"))
        app._handle(("javas", []))
        app._handle(("launcher_update", ("v9.9.9", "https://example.com")))
        res = types.SimpleNamespace(ok=True, summary=lambda: "ok")
        app._handle(("done", res, True))
        self.msg.showinfo.assert_called()
        app._handle(("done", types.SimpleNamespace(ok=False, summary=lambda: "err"), False))
        self.msg.showwarning.assert_called()
        app._handle(("cancelled",))
        app._handle(("error", "что-то пошло не так"))
        self.msg.showerror.assert_called()

    def test_settings_roundtrip(self):
        app = self.make_app()
        app.var_nick.set("  Jeff  ")
        app.var_mode.set("Офлайн (по нику)")
        app.var_mem.set(8192)
        app.var_repo.set("me/pack")
        app.var_exe.set("  D:/Games/Freesm/FreesmLauncher.exe ")
        app.var_instdir.set(" D:/Games/Freesm/instances ")
        app.var_inst.set("Имперская сборка")
        app._collect()
        self.assertEqual(app.s.freesm_exe, "D:/Games/Freesm/FreesmLauncher.exe")
        self.assertEqual(app.s.instances_dir, "D:/Games/Freesm/instances")
        self.assertEqual(app.s.instance_id, "Имперская сборка")
        app.var_inst.set(self.gui.NEW_INSTANCE_LABEL)
        app.var_memmanage.set(True)
        app._collect()
        self.assertEqual(app.s.instance_id, "")
        self.assertTrue(app.s.manage_memory)
        app._mem_toggle()
        app._refresh_instances()
        self.assertEqual(app.s.nickname, "Jeff")
        self.assertEqual(app.s.memory_mb, 8192)
        self.assertEqual(app.s.pack_repo, "me/pack")
        self.assertEqual(app.s.account_mode, "offline")
        app.var_mode.set("Microsoft")
        app._collect()
        self.assertEqual(app.s.account_mode, "microsoft")
        app._on_mode()
        app._mem_label()

    def test_folder_mode_switch(self):
        app = self.make_app()
        app.manifest = self.manifest()
        app.var_kind.set(self.gui.KIND_FOLDER)
        app.var_folder.set(' "D:/Mods/Profiles/Imperial" ')
        app._on_kind()                       # перерисовывает главную кнопку для режима «папка»
        self.assertEqual(app.s.play_kind, "folder")
        self.assertEqual(app.s.folder_dir, "D:/Mods/Profiles/Imperial")
        app.profile_paths = {"Imperial": Path("D:/x/Imperial")}
        app._pick_modrinth_profile("Imperial")
        self.assertEqual(app.var_folder.get(), str(Path("D:/x/Imperial")))
        app._check_account()
        app._refresh_modrinth_profiles()
        app.var_kind.set(self.gui.KIND_FREESM)
        app._on_kind()
        self.assertEqual(app.s.play_kind, "freesm")

    def test_browse_handlers(self):
        import tempfile
        app = self.make_app()
        with tempfile.TemporaryDirectory() as d:
            inst = Path(d) / "instances" / "Моя сборка"
            inst.mkdir(parents=True)
            (inst / "instance.cfg").write_text("[General]\n")
            self.gui.filedialog.askdirectory.return_value = str(inst)   # выбрали папку самого экземпляра
            app._browse_instdir()
            self.assertEqual(app.var_instdir.get(), str(Path(d) / "instances"))
            self.assertEqual(app.var_inst.get(), "Моя сборка")
            self.gui.filedialog.askopenfilename.return_value = str(Path(d) / "FreesmLauncher.exe")
            app.var_instdir.set("")
            app._browse_exe()
            self.assertTrue(app.var_exe.get().endswith("FreesmLauncher.exe"))
        app._detect_freesm()     # на Linux ничего не найдёт, но не должен падать

    def test_java_menu_and_browse(self):
        app = self.make_app()
        from launcher.javafind import JavaInfo
        app.javas = [JavaInfo("C:/J/21/bin/javaw.exe", 21, "21.0.7")]
        app._set_java_menu()
        self.gui.filedialog.askopenfilename.return_value = "C:/Java/bin/javaw.exe"
        app._browse_java()
        app._collect()
        self.assertEqual(app.s.java_path, "C:/Java/bin/javaw.exe")
        app.var_java.set(self.gui.AUTO_JAVA_LABEL)
        app._collect()
        self.assertEqual(app.s.java_path, "")

    def test_start_flow_calls_worker(self):
        app = self.make_app()
        app.manifest = self.manifest()
        import threading
        started = []
        orig = threading.Thread.start
        threading.Thread.start = lambda self: started.append(self)
        try:
            app._start(play=False)
        finally:
            threading.Thread.start = orig
        self.assertTrue(app.busy)
        self.assertEqual(len(started), 1)
        app._finish()
        self.assertFalse(app.busy)

    def test_gear_math(self):
        cv = MagicMock()
        self.gui.draw_gear(cv, 10, 10, 20, 15, 12, 0.5, "#fff", "#000", "#111")
        self.assertEqual(cv.create_polygon.call_count, 1)
        pts = cv.create_polygon.call_args[0][0]
        self.assertEqual(len(pts), 12 * 4 * 2)


if __name__ == "__main__":
    unittest.main()
