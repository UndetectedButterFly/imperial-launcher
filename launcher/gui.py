"""Окно лаунчера (CustomTkinter).

Идея оформления: тёмный «чугун» и латунь — в тон паровым механизмам Create.
Единственный акцент — пара шестерён слева: стоят, пока лаунчер ждёт, и вращаются,
пока идёт установка или обновление (движение отвечает на действие пользователя).
"""
from __future__ import annotations

import math
import queue
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from . import __version__, core, freesm, javafind, net, others, syncer
from . import settings as cfg
from .manifest import Manifest, version_tuple

# --- палитра ---
BG = "#1B2027"        # чугун
PANEL = "#232A32"     # патина
PANEL2 = "#2D363F"
BORDER = "#3A4550"
BRASS = "#C99A4B"
BRASS_HOVER = "#DDB367"
BRASS_DARK = "#8F6A2E"
TEXT = "#ECE5D3"
MUTED = "#98A2AD"
OK = "#6FB39E"        # патина-медь
WARN = "#DDAA4A"
ERR = "#CF6A52"

TITLE_FONT = "Georgia"
BODY_FONT = "Segoe UI"

AUTO_JAVA_LABEL = "Выберет Freesm (по умолчанию)"
NEW_INSTANCE_LABEL = "Создать новый экземпляр сборки"
KIND_FREESM = "Freesm или Prism"
KIND_FOLDER = "Modrinth App или другая папка"
NO_PROFILES_LABEL = "Профили Modrinth App не найдены"


def draw_gear(cv: tk.Canvas, cx: float, cy: float, r_out: float, r_in: float,
              teeth: int, angle: float, fill: str, hole: str, bg: str) -> None:
    step = 2 * math.pi / teeth
    pts = []
    for i in range(teeth):
        a0 = angle + i * step
        for frac, r in ((0.0, r_in), (0.12, r_out), (0.38, r_out), (0.5, r_in)):
            a = a0 + frac * step
            pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
    cv.create_polygon(pts, fill=fill, outline=BRASS_DARK, width=2)
    cv.create_oval(cx - r_in * 0.62, cy - r_in * 0.62, cx + r_in * 0.62, cy + r_in * 0.62,
                   fill=bg, outline=BRASS_DARK, width=2)
    cv.create_oval(cx - r_in * 0.22, cy - r_in * 0.22, cx + r_in * 0.22, cy + r_in * 0.22,
                   fill=hole, outline=BRASS_DARK, width=2)


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title(f"Империя — лаунчер {__version__}")
        self.configure(fg_color=BG)
        self.geometry("1020x660")
        self.minsize(940, 600)

        self.log_file = core.setup_logging()
        self.s = cfg.load()
        self.q: "queue.Queue[tuple]" = queue.Queue()
        self.busy = False
        self.cancel = threading.Event()
        self.manifest: Manifest | None = None
        self.javas: list[javafind.JavaInfo] = []
        self.java_labels: dict[str, str] = {}
        self.angle = 0.0
        self.first_install = False

        self.f_title = ctk.CTkFont(family=TITLE_FONT, size=36, weight="bold")
        self.f_h = ctk.CTkFont(family=TITLE_FONT, size=20, weight="bold")
        self.f_body = ctk.CTkFont(family=BODY_FONT, size=14)
        self.f_small = ctk.CTkFont(family=BODY_FONT, size=12)
        self.f_btn = ctk.CTkFont(family=BODY_FONT, size=16, weight="bold")

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_left()
        self._build_right()
        self._load_settings_into_widgets()
        self._draw_gears()
        self.after(100, self._poll)
        self.after(300, self._set_icon)
        threading.Thread(target=self._startup_tasks, daemon=True).start()

    def _set_icon(self) -> None:
        try:  # CustomTkinter сам ставит иконку через 200 мс, поэтому позже
            base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
            ico = base / "assets" / "icon.ico"
            if ico.exists() and sys.platform.startswith("win"):
                self.iconbitmap(str(ico))
        except Exception:
            pass

    # ---------------- левая панель ----------------
    def _build_left(self) -> None:
        left = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=0, width=350)
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_propagate(False)
        left.grid_columnconfigure(0, weight=1)

        self.gears = tk.Canvas(left, width=230, height=170, bg=PANEL, highlightthickness=0)
        self.gears.grid(row=0, column=0, pady=(26, 4))

        ctk.CTkLabel(left, text="Империя", font=self.f_title, text_color=TEXT).grid(row=1, column=0)
        self.lbl_sub = ctk.CTkLabel(left, text="The Sacred IT Empire", font=self.f_body, text_color=MUTED)
        self.lbl_sub.grid(row=2, column=0, pady=(0, 18))

        self.lbl_status = ctk.CTkLabel(left, text="Проверяю сборку…", font=self.f_h, text_color=TEXT,
                                       wraplength=300, justify="center")
        self.lbl_status.grid(row=3, column=0, padx=24)
        self.lbl_detail = ctk.CTkLabel(left, text="", font=self.f_small, text_color=MUTED,
                                       wraplength=300, justify="center")
        self.lbl_detail.grid(row=4, column=0, padx=24, pady=(4, 18))

        self.btn_main = ctk.CTkButton(left, text="Подождите…", font=self.f_btn, height=54, corner_radius=10,
                                      fg_color=BRASS, hover_color=BRASS_HOVER, text_color="#1B1408",
                                      command=self._on_main)
        self.btn_main.grid(row=5, column=0, padx=28, sticky="ew")
        self.btn_update = ctk.CTkButton(left, text="Только обновить", font=self.f_body, height=40,
                                        corner_radius=10, fg_color="transparent", border_width=1,
                                        border_color=BORDER, hover_color=PANEL2, text_color=TEXT,
                                        command=lambda: self._start(play=False))
        self.btn_update.grid(row=6, column=0, padx=28, pady=(10, 0), sticky="ew")

        self.bar = ctk.CTkProgressBar(left, height=8, corner_radius=4, progress_color=BRASS, fg_color=PANEL2)
        self.bar.set(0)
        self.bar.grid(row=7, column=0, padx=28, pady=(22, 4), sticky="ew")
        self.lbl_prog = ctk.CTkLabel(left, text="", font=self.f_small, text_color=MUTED, wraplength=300)
        self.lbl_prog.grid(row=8, column=0, padx=28)
        self.btn_cancel = ctk.CTkButton(left, text="Отмена", font=self.f_small, height=30, width=100,
                                        fg_color="transparent", border_width=1, border_color=BORDER,
                                        hover_color=PANEL2, text_color=MUTED, command=self._on_cancel)

        left.grid_rowconfigure(9, weight=1)
        self.lbl_update = ctk.CTkLabel(left, text="", font=self.f_small, text_color=WARN, cursor="hand2",
                                       wraplength=300)
        self.lbl_update.grid(row=10, column=0, padx=24, pady=(0, 2))
        ctk.CTkLabel(left, text=f"Лаунчер {__version__}", font=self.f_small, text_color=MUTED
                     ).grid(row=11, column=0, pady=(0, 14))

    def _draw_gears(self) -> None:
        cv = self.gears
        cv.delete("all")
        draw_gear(cv, 98, 84, 66, 54, 12, self.angle, BRASS, PANEL, PANEL)
        # малая шестерня вращается в обратную сторону и со сдвигом фазы, как в зацеплении
        draw_gear(cv, 176, 130, 33, 26, 8, -self.angle * 1.5 + math.pi / 8, "#A9B2BC", PANEL, PANEL)

    def _tick_gears(self) -> None:
        if not self.busy:
            self._draw_gears()
            return
        self.angle += 0.06
        self._draw_gears()
        self.after(40, self._tick_gears)

    # ---------------- правая часть ----------------
    def _build_right(self) -> None:
        right = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        right.grid(row=0, column=1, sticky="nsew", padx=(20, 24), pady=20)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        self.tabs = ctk.CTkSegmentedButton(
            right, values=["Что нового", "Игрок", "Настройки", "Журнал"], command=self._show_page,
            font=self.f_body, selected_color=BRASS, selected_hover_color=BRASS_HOVER,
            unselected_color=PANEL, unselected_hover_color=PANEL2, fg_color=PANEL, text_color=TEXT)
        self.tabs.grid(row=0, column=0, sticky="w", pady=(0, 14))

        self.page_box = ctk.CTkFrame(right, fg_color=PANEL, corner_radius=14)
        self.page_box.grid(row=1, column=0, sticky="nsew")
        self.page_box.grid_columnconfigure(0, weight=1)
        self.page_box.grid_rowconfigure(0, weight=1)
        self.pages: dict[str, ctk.CTkFrame] = {}
        for name in ("Что нового", "Игрок", "Настройки", "Журнал"):
            if name == "Настройки":  # длинная страница — с прокруткой
                f = ctk.CTkScrollableFrame(self.page_box, fg_color="transparent")
            else:
                f = ctk.CTkFrame(self.page_box, fg_color="transparent")
            f.grid_columnconfigure(0, weight=1)
            self.pages[name] = f
        self._build_news(self.pages["Что нового"])
        self._build_player(self.pages["Игрок"])
        self._build_settings(self.pages["Настройки"])
        self._build_log(self.pages["Журнал"])
        self.tabs.set("Что нового")
        self._show_page("Что нового")

    def _show_page(self, name: str) -> None:
        for f in self.pages.values():
            f.grid_remove()
        self.pages[name].grid(row=0, column=0, sticky="nsew", padx=22, pady=20)

    def _box(self, master, **kw) -> ctk.CTkTextbox:
        return ctk.CTkTextbox(master, fg_color=PANEL2, text_color=TEXT, font=self.f_body, wrap="word",
                              corner_radius=10, border_width=0, **kw)

    def _build_news(self, p) -> None:
        p.grid_rowconfigure(1, weight=1)
        self.lbl_news = ctk.CTkLabel(p, text="Что нового в сборке", font=self.f_h, text_color=TEXT, anchor="w")
        self.lbl_news.grid(row=0, column=0, sticky="w", pady=(0, 10))
        self.txt_news = self._box(p)
        self.txt_news.grid(row=1, column=0, sticky="nsew")
        self._set_text(self.txt_news, "Список изменений появится после проверки сборки.")

    def _row_label(self, p, row, text, hint=None) -> None:
        ctk.CTkLabel(p, text=text, font=self.f_body, text_color=TEXT, anchor="w"
                     ).grid(row=row, column=0, sticky="w", pady=(14, 2))
        if hint:
            ctk.CTkLabel(p, text=hint, font=self.f_small, text_color=MUTED, anchor="w", wraplength=560,
                         justify="left").grid(row=row + 1, column=0, sticky="w")

    def _entry(self, p, var, **kw) -> ctk.CTkEntry:
        return ctk.CTkEntry(p, textvariable=var, height=36, fg_color=PANEL2, border_color=BORDER,
                            text_color=TEXT, font=self.f_body, **kw)

    def _ghost_btn(self, p, text, cmd, width=140) -> ctk.CTkButton:
        return ctk.CTkButton(p, text=text, command=cmd, width=width, height=36, font=self.f_body,
                             fg_color="transparent", border_width=1, border_color=BORDER,
                             hover_color=PANEL2, text_color=TEXT)

    def _build_player(self, p) -> None:
        ctk.CTkLabel(p, text="Игрок", font=self.f_h, text_color=TEXT, anchor="w").grid(row=0, column=0, sticky="w")
        self.var_mode = tk.StringVar(value="Офлайн (по нику)")
        self.seg_mode = ctk.CTkSegmentedButton(
            p, values=["Офлайн (по нику)", "Microsoft"], variable=self.var_mode, command=self._on_mode,
            font=self.f_body, selected_color=BRASS, selected_hover_color=BRASS_HOVER,
            unselected_color=PANEL2, unselected_hover_color=BORDER, fg_color=PANEL2, text_color=TEXT)
        self.seg_mode.grid(row=1, column=0, sticky="w", pady=(14, 0))

        self.var_nick = tk.StringVar()
        self.lbl_nick = ctk.CTkLabel(p, text="Ник", font=self.f_body, text_color=TEXT, anchor="w")
        self.lbl_nick.grid(row=2, column=0, sticky="w", pady=(16, 2))
        self.ent_nick = self._entry(p, self.var_nick, width=300, placeholder_text="Ваш ник в игре")
        self.ent_nick.grid(row=3, column=0, sticky="w")

        self.lbl_mode_hint = ctk.CTkLabel(p, text="", font=self.f_small, text_color=MUTED, anchor="w",
                                          wraplength=560, justify="left")
        self.lbl_mode_hint.grid(row=4, column=0, sticky="w", pady=(10, 0))

        row = ctk.CTkFrame(p, fg_color="transparent")
        row.grid(row=5, column=0, sticky="w", pady=(16, 0))
        self._ghost_btn(row, "Проверить аккаунт", self._check_account, 170).grid(row=0, column=0)
        self._ghost_btn(row, "Открыть Freesm", self._open_freesm, 150).grid(row=0, column=1, padx=10)
        self.lbl_acc = ctk.CTkLabel(p, text="", font=self.f_small, text_color=MUTED, anchor="w",
                                    wraplength=560, justify="left")
        self.lbl_acc.grid(row=6, column=0, sticky="w", pady=(10, 0))
        self._ghost_btn(p, "Сохранить", self._save_clicked, 130).grid(row=7, column=0, sticky="w", pady=(18, 0))
        self._on_mode()

    def _build_settings(self, p) -> None:
        ctk.CTkLabel(p, text="Настройки", font=self.f_h, text_color=TEXT, anchor="w").grid(row=0, column=0, sticky="w")

        self._row_label(p, 1, "Java", "Для этой сборки нужна Java 21. Если не выбрано, Java подберёт Freesm.")
        jr = ctk.CTkFrame(p, fg_color="transparent")
        jr.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        jr.grid_columnconfigure(0, weight=1)
        self.var_java = tk.StringVar(value=AUTO_JAVA_LABEL)
        self.opt_java = ctk.CTkOptionMenu(
            jr, values=[AUTO_JAVA_LABEL], variable=self.var_java, dynamic_resizing=False, height=36,
            fg_color=PANEL2, button_color=BORDER, button_hover_color=BRASS_DARK, text_color=TEXT,
            dropdown_fg_color=PANEL2, dropdown_text_color=TEXT, dropdown_hover_color=BORDER, font=self.f_small)
        self.opt_java.grid(row=0, column=0, sticky="ew")
        self._ghost_btn(jr, "Подобрать", self._auto_java, 100).grid(row=0, column=1, padx=(8, 0))
        self._ghost_btn(jr, "Файл…", self._browse_java, 80).grid(row=0, column=2, padx=(8, 0))

        self._row_label(p, 4, "Память для игры")
        mr = ctk.CTkFrame(p, fg_color="transparent")
        mr.grid(row=5, column=0, sticky="ew")
        mr.grid_columnconfigure(0, weight=1)
        self.var_mem = tk.IntVar(value=6144)
        self.sld_mem = ctk.CTkSlider(mr, from_=2048, to=12288, number_of_steps=20, variable=self.var_mem,
                                     command=lambda v: self._mem_label(), progress_color=BRASS,
                                     button_color=BRASS, button_hover_color=BRASS_HOVER, fg_color=PANEL2)
        self.sld_mem.grid(row=0, column=0, sticky="ew")
        self.lbl_mem = ctk.CTkLabel(mr, text="6 ГБ", font=self.f_body, text_color=TEXT, width=70)
        self.lbl_mem.grid(row=0, column=1, padx=(12, 0))
        self.var_memmanage = tk.BooleanVar(value=False)
        ctk.CTkSwitch(mr, text="Менять память экземпляра (иначе остаётся как в Freesm)",
                      variable=self.var_memmanage, command=self._mem_toggle, font=self.f_body,
                      text_color=TEXT, progress_color=BRASS, button_color=TEXT, fg_color=PANEL2
                      ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self.var_join = tk.BooleanVar(value=True)
        self.var_pre = tk.BooleanVar(value=False)
        self.var_quar = tk.BooleanVar(value=False)
        for i, (var, text) in enumerate((
                (self.var_join, "Заходить на сервер сразу после запуска"),
                (self.var_pre, "Обновлять сборку и при запуске прямо из Freesm"),
                (self.var_quar, "Убирать посторонние моды в карантин (не удаляя)"))):
            ctk.CTkSwitch(p, text=text, variable=var, font=self.f_body, text_color=TEXT, progress_color=BRASS,
                          button_color=TEXT, fg_color=PANEL2).grid(row=6 + i, column=0, sticky="w", pady=(14 if i == 0 else 8, 0))

        self._row_label(p, 9, "Папка установки")
        fr = ctk.CTkFrame(p, fg_color="transparent")
        fr.grid(row=10, column=0, sticky="ew")
        fr.grid_columnconfigure(0, weight=1)
        self.var_root = tk.StringVar()
        self._entry(fr, self.var_root).grid(row=0, column=0, sticky="ew")
        self._ghost_btn(fr, "Выбрать…", self._browse_root, 100).grid(row=0, column=1, padx=(8, 0))

        self._row_label(p, 11, "Репозиторий сборки на GitHub", "Формат: владелец/репозиторий")
        self.var_repo = tk.StringVar()
        self._entry(p, self.var_repo, width=320).grid(row=13, column=0, sticky="w")

        kr = ctk.CTkFrame(p, fg_color="transparent")
        kr.grid(row=14, column=0, sticky="ew", pady=(24, 0))
        ctk.CTkLabel(kr, text="Как вы запускаете игру", font=self.f_body, text_color=TEXT, anchor="w"
                     ).grid(row=0, column=0, sticky="w")
        self.var_kind = tk.StringVar(value=KIND_FREESM)
        self.seg_kind = ctk.CTkSegmentedButton(
            kr, values=[KIND_FREESM, KIND_FOLDER], variable=self.var_kind, command=self._on_kind,
            font=self.f_small, selected_color=BRASS, selected_hover_color=BRASS_HOVER,
            unselected_color=PANEL2, unselected_hover_color=BORDER, fg_color=PANEL2, text_color=TEXT)
        self.seg_kind.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.lbl_kind = ctk.CTkLabel(kr, text="", font=self.f_small, text_color=MUTED, anchor="w",
                                     wraplength=560, justify="left")
        self.lbl_kind.grid(row=2, column=0, sticky="w", pady=(6, 0))

        # --- Freesm / Prism ---
        self.grp_freesm = ctk.CTkFrame(p, fg_color="transparent")
        self.grp_freesm.grid(row=15, column=0, sticky="ew")
        self.grp_freesm.grid_columnconfigure(0, weight=1)
        gf = self.grp_freesm
        self._row_label(gf, 0, "Freesm или Prism (необязательно)",
                        "Файл FreesmLauncher.exe или prismlauncher.exe, если лаунчер уже установлен. "
                        "Пусто: лаунчер поставит свою копию Freesm.")
        fr2 = ctk.CTkFrame(gf, fg_color="transparent")
        fr2.grid(row=2, column=0, sticky="ew")
        fr2.grid_columnconfigure(0, weight=1)
        self.var_exe = tk.StringVar()
        self._entry(fr2, self.var_exe).grid(row=0, column=0, sticky="ew")
        self._ghost_btn(fr2, "Файл…", self._browse_exe, 80).grid(row=0, column=1, padx=(8, 0))
        self._ghost_btn(fr2, "Найти", self._detect_freesm, 80).grid(row=0, column=2, padx=(8, 0))

        self._row_label(gf, 3, "Папка instances (необязательно)",
                        "Нужна, если лаунчер установлен как обычная программа. В нём: правый клик по экземпляру → "
                        "«Папка». Подойдёт и папка самого экземпляра, и папка instances.")
        fr3 = ctk.CTkFrame(gf, fg_color="transparent")
        fr3.grid(row=5, column=0, sticky="ew")
        fr3.grid_columnconfigure(0, weight=1)
        self.var_instdir = tk.StringVar()
        self._entry(fr3, self.var_instdir).grid(row=0, column=0, sticky="ew")
        self._ghost_btn(fr3, "Папка…", self._browse_instdir, 90).grid(row=0, column=1, padx=(8, 0))

        self._row_label(gf, 6, "Экземпляр",
                        "Выберите свой готовый экземпляр, чтобы лаунчер обновлял его, а не создавал новый.")
        ir = ctk.CTkFrame(gf, fg_color="transparent")
        ir.grid(row=8, column=0, sticky="ew")
        ir.grid_columnconfigure(0, weight=1)
        self.var_inst = tk.StringVar(value=NEW_INSTANCE_LABEL)
        self.opt_inst = ctk.CTkOptionMenu(
            ir, values=[NEW_INSTANCE_LABEL], variable=self.var_inst, dynamic_resizing=False, height=36,
            fg_color=PANEL2, button_color=BORDER, button_hover_color=BRASS_DARK, text_color=TEXT,
            dropdown_fg_color=PANEL2, dropdown_text_color=TEXT, dropdown_hover_color=BORDER, font=self.f_small)
        self.opt_inst.grid(row=0, column=0, sticky="ew")
        self._ghost_btn(ir, "Обновить список", self._refresh_instances, 150).grid(row=0, column=1, padx=(8, 0))

        # --- Modrinth App / другая папка ---
        self.grp_folder = ctk.CTkFrame(p, fg_color="transparent")
        self.grp_folder.grid(row=16, column=0, sticky="ew")
        self.grp_folder.grid_columnconfigure(0, weight=1)
        gd = self.grp_folder
        self._row_label(gd, 0, "Папка профиля или игры",
                        "Для Modrinth App: откройте профиль → значок папки → скопируйте путь. Лаунчер обновит в ней "
                        "моды и конфиги, а игру вы запускаете сами.")
        fr4 = ctk.CTkFrame(gd, fg_color="transparent")
        fr4.grid(row=2, column=0, sticky="ew")
        fr4.grid_columnconfigure(0, weight=1)
        self.var_folder = tk.StringVar()
        self._entry(fr4, self.var_folder).grid(row=0, column=0, sticky="ew")
        self._ghost_btn(fr4, "Папка…", self._browse_folder, 90).grid(row=0, column=1, padx=(8, 0))
        self._row_label(gd, 3, "Профили Modrinth App")
        self.var_mprofile = tk.StringVar(value=NO_PROFILES_LABEL)
        self.profile_paths: dict = {}
        self.opt_mprofile = ctk.CTkOptionMenu(
            gd, values=[NO_PROFILES_LABEL], variable=self.var_mprofile, command=self._pick_modrinth_profile,
            dynamic_resizing=False, height=36, width=360, fg_color=PANEL2, button_color=BORDER,
            button_hover_color=BRASS_DARK, text_color=TEXT, dropdown_fg_color=PANEL2, dropdown_text_color=TEXT,
            dropdown_hover_color=BORDER, font=self.f_small)
        self.opt_mprofile.grid(row=4, column=0, sticky="w")

        brow = ctk.CTkFrame(p, fg_color="transparent")
        brow.grid(row=17, column=0, sticky="w", pady=(18, 0))
        self._ghost_btn(brow, "Сохранить", self._save_clicked, 130).grid(row=0, column=0)
        self._ghost_btn(brow, "Папка игры", self._open_game_dir, 130).grid(row=0, column=1, padx=10)

    def _build_log(self, p) -> None:
        p.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(p, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ctk.CTkLabel(top, text="Журнал", font=self.f_h, text_color=TEXT).grid(row=0, column=0, sticky="w")
        self._ghost_btn(top, "Открыть файл журнала", self._open_log_file, 190).grid(row=0, column=1, padx=16)
        self.txt_log = self._box(p)
        self.txt_log.grid(row=1, column=0, sticky="nsew")
        self.txt_log.configure(state="disabled")

    # ---------------- вспомогательное ----------------
    def _set_text(self, box: ctk.CTkTextbox, text: str) -> None:
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", text)
        box.configure(state="disabled")

    def _append_log(self, text: str) -> None:
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", text + "\n")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def log(self, text: str) -> None:
        """Потокобезопасный лог: в очередь и в файл."""
        self.log_file.info(text)
        self.q.put(("log", text))

    def _mem_toggle(self) -> None:
        self.sld_mem.configure(state="normal" if self.var_memmanage.get() else "disabled")

    def _instances_folder(self) -> Path | None:
        """Папка instances по текущим полям окна (без записи в настройки)."""
        d = self.var_instdir.get().strip()
        if d:
            return Path(d)
        exe_txt = self.var_exe.get().strip()
        if exe_txt:
            p = Path(exe_txt)
            exe = p if p.is_file() else freesm.find_freesm_exe(p)
            return freesm.guess_instances_dir(exe) if exe else None
        own = freesm.find_freesm_exe(Path(self.var_root.get().strip() or cfg.default_root()) / "Freesm")
        return freesm.guess_instances_dir(own) if own else None

    def _refresh_instances(self) -> None:
        """Подтягивает список экземпляров из выбранной папки instances."""
        folder = self._instances_folder()
        names = freesm.list_instances(folder) if folder else []
        cur = self.var_inst.get()
        values = [NEW_INSTANCE_LABEL] + names
        if cur != NEW_INSTANCE_LABEL and cur not in values:
            values.append(cur)
        self.opt_inst.configure(values=values)

    def _mem_label(self) -> None:
        self.lbl_mem.configure(text=f"{self.var_mem.get() / 1024:.1f} ГБ".replace(".0", ""))

    def _on_mode(self, *_a) -> None:
        offline = self.var_mode.get().startswith("Офлайн")
        if offline:
            self.lbl_nick.grid()
            self.ent_nick.grid()
            self.lbl_mode_hint.configure(
                text="Сервер работает без проверки лицензии. Ник должен совпадать с аккаунтом, "
                     "созданным в Freesm (Аккаунты → Добавить оффлайн).")
        else:
            self.lbl_nick.grid_remove()
            self.ent_nick.grid_remove()
            self.lbl_mode_hint.configure(
                text="Войдите в Microsoft-аккаунт в самом Freesm (Аккаунты → Добавить Microsoft). "
                     "Лаунчер запустит игру с аккаунтом, выбранным в Freesm по умолчанию.")

    def _load_settings_into_widgets(self) -> None:
        s = self.s
        self.var_mode.set("Офлайн (по нику)" if s.account_mode == "offline" else "Microsoft")
        self.var_nick.set(s.nickname)
        self.var_mem.set(int(s.memory_mb))
        self.var_join.set(s.join_server)
        self.var_pre.set(s.auto_prelaunch)
        self.var_quar.set(s.quarantine_unknown)
        self.var_root.set(s.root_dir)
        self.var_repo.set(s.pack_repo)
        self.var_exe.set(s.freesm_exe or s.freesm_dir)
        self.var_instdir.set(s.instances_dir)
        self.var_kind.set(KIND_FOLDER if s.play_kind == "folder" else KIND_FREESM)
        self.var_folder.set(s.folder_dir)
        self._refresh_modrinth_profiles()
        self._on_kind()
        self.var_inst.set(s.instance_id or NEW_INSTANCE_LABEL)
        self.var_memmanage.set(s.manage_memory)
        self._mem_toggle()
        self._refresh_instances()
        self._mem_label()
        self._on_mode()

    def _collect(self) -> None:
        s = self.s
        s.account_mode = "offline" if self.var_mode.get().startswith("Офлайн") else "microsoft"
        s.nickname = self.var_nick.get().strip()
        s.memory_mb = int(self.var_mem.get())
        s.join_server = bool(self.var_join.get())
        s.auto_prelaunch = bool(self.var_pre.get())
        s.quarantine_unknown = bool(self.var_quar.get())
        s.root_dir = self.var_root.get().strip() or str(cfg.default_root())
        repo = self.var_repo.get().strip()
        if repo:
            s.pack_repo = repo
        exe_txt = self.var_exe.get().strip()
        s.freesm_exe, s.freesm_dir = exe_txt, ""     # поле принимает и файл, и папку с exe
        s.instances_dir = self.var_instdir.get().strip()
        lab = self.var_inst.get().strip()
        s.instance_id = "" if lab in ("", NEW_INSTANCE_LABEL) else lab
        s.manage_memory = bool(self.var_memmanage.get())
        s.play_kind = "folder" if self.var_kind.get() == KIND_FOLDER else "freesm"
        s.folder_dir = self.var_folder.get().strip().strip('"')
        label = self.var_java.get()
        s.java_path = "" if label == AUTO_JAVA_LABEL else self.java_labels.get(label, s.java_path)

    def _save_clicked(self) -> None:
        self._collect()
        cfg.save(self.s)
        self.lbl_prog.configure(text="Настройки сохранены")
        threading.Thread(target=self._refresh_manifest, daemon=True).start()

    def _browse_root(self) -> None:
        d = filedialog.askdirectory(title="Папка установки", initialdir=self.var_root.get() or str(Path.home()))
        if d:
            self.var_root.set(d)

    def _on_kind(self, *_a) -> None:
        folder = self.var_kind.get() == KIND_FOLDER
        if folder:
            self.grp_freesm.grid_remove()
            self.grp_folder.grid()
            self.lbl_kind.configure(text="Лаунчер только проверит и обновит файлы сборки в папке. Java, память, "
                                         "версию игры и запуск вы настраиваете в своём лаунчере (кнопки «Играть» тут не будет).")
        else:
            self.grp_folder.grid_remove()
            self.grp_freesm.grid()
            self.lbl_kind.configure(text="Лаунчер сам обновит сборку и запустит игру через Freesm или Prism.")
        if self.manifest is not None:
            self._collect()
            self._apply_manifest(self.manifest)

    def _refresh_modrinth_profiles(self) -> None:
        self.profile_paths = {p.name: p for p in others.modrinth_profiles()}
        names = list(self.profile_paths) or [NO_PROFILES_LABEL]
        self.opt_mprofile.configure(values=names)
        self.var_mprofile.set(names[0] if not self.profile_paths else
                              next((n for n, p in self.profile_paths.items() if str(p) == self.var_folder.get()), names[0]))

    def _pick_modrinth_profile(self, name: str) -> None:
        p = self.profile_paths.get(name)
        if p:
            self.var_folder.set(str(p))

    def _browse_folder(self) -> None:
        d = filedialog.askdirectory(title="Папка профиля или игры (где лежит mods)",
                                    initialdir=self.var_folder.get() or str(others.modrinth_app_dir()))
        if d:
            self.var_folder.set(d)

    def _autofill_instdir(self, exe: Path) -> None:
        if not self.var_instdir.get().strip():
            g = freesm.guess_instances_dir(exe)
            if g:
                self.var_instdir.set(str(g))

    def _browse_exe(self) -> None:
        f = filedialog.askopenfilename(title="Выберите FreesmLauncher.exe",
                                       filetypes=[("Freesm Launcher", "FreesmLauncher.exe"), ("Программы", "*.exe")])
        if f:
            self.var_exe.set(f)
            self._autofill_instdir(Path(f))
            self._refresh_instances()

    def _detect_freesm(self) -> None:
        exe = freesm.detect_installed_exe()
        if exe is None:
            messagebox.showinfo(
                "Поиск Freesm",
                "Автоматически найти не получилось. Нажмите «Файл…» и выберите FreesmLauncher.exe "
                "(подсказка: ярлык Freesm на рабочем столе → правый клик → «Расположение файла»).")
            return
        self.var_exe.set(str(exe))
        self._autofill_instdir(exe)
        self._refresh_instances()

    def _browse_instdir(self) -> None:
        d = filedialog.askdirectory(title="Папка instances или папка вашего экземпляра",
                                    initialdir=self.var_instdir.get() or str(Path.home()))
        if not d:
            return
        p = Path(d)
        if (p / "instance.cfg").is_file():     # выбрали папку самого экземпляра
            self.var_instdir.set(str(p.parent))
            self._refresh_instances()
            self.var_inst.set(p.name)
        else:
            self.var_instdir.set(str(p))
            self._refresh_instances()

    def _browse_java(self) -> None:
        f = filedialog.askopenfilename(title="Выберите javaw.exe (Java 21)",
                                       filetypes=[("Java", "javaw.exe java.exe"), ("Все файлы", "*.*")])
        if not f:
            return
        label = f"Выбрано вручную — {f}"
        self.java_labels[label] = f
        self._set_java_menu(select=label)

    def _auto_java(self) -> None:
        if not self.manifest:
            messagebox.showinfo("Java", "Сначала дождитесь проверки сборки.")
            return
        j = core.auto_java(self.manifest)
        if not j:
            messagebox.showwarning("Java", f"Java {self.manifest.java_major} не найдена на этом компьютере. "
                                           "Оставьте «Выберет Freesm», он скачает подходящую сам.")
            return
        self._set_java_menu(select=j.label())

    def _set_java_menu(self, select: str | None = None) -> None:
        for j in self.javas:
            self.java_labels[j.label()] = j.path
        values = [AUTO_JAVA_LABEL] + list(self.java_labels.keys())
        self.opt_java.configure(values=values)
        if select:
            self.var_java.set(select)
        elif self.s.java_path:
            match = next((k for k, v in self.java_labels.items() if v == self.s.java_path), None)
            if match is None:
                match = f"Из настроек — {self.s.java_path}"
                self.java_labels[match] = self.s.java_path
                self.opt_java.configure(values=[AUTO_JAVA_LABEL] + list(self.java_labels.keys()))
            self.var_java.set(match)
        else:
            self.var_java.set(AUTO_JAVA_LABEL)

    def _game_dir(self) -> Path:
        m = self.manifest
        try:
            if self.s.play_kind == "folder":
                return core.folder_game_dir(self.s)
            if m:
                return core.paths_for(self.s, m)[2]
            return core.resolve_paths(self.s).instances_dir
        except core.CoreError as e:
            messagebox.showinfo("Папка игры", str(e))
            return Path("")

    def _open_game_dir(self) -> None:
        self._collect()
        d = self._game_dir()
        if str(d) not in ("", ".") and d.exists():
            _open_path(d)
        else:
            messagebox.showinfo("Папка игры", "Сборка ещё не установлена.")

    def _open_log_file(self) -> None:
        _open_path(cfg.app_dir() / "launcher.log")

    def _open_freesm(self) -> None:
        self._collect()
        try:
            paths = core.resolve_paths(self.s)
        except core.CoreError as e:
            messagebox.showinfo("Freesm", str(e))
            return
        if paths.exe is None:
            messagebox.showinfo("Freesm", "Freesm ещё не установлен: нажмите «Установить сборку».")
            return
        freesm.open_app(paths.exe, paths.data_dir)

    def _check_account(self) -> None:
        self._collect()
        if self.s.play_kind == "folder":
            self.lbl_acc.configure(text="В режиме «Modrinth App или другая папка» аккаунт не нужен: игру запускаете вы.",
                                   text_color=MUTED)
            return
        try:
            root = core.resolve_paths(self.s).accounts_root
        except core.CoreError as e:
            self.lbl_acc.configure(text=str(e), text_color=WARN)
            return
        names = freesm.account_names(root)
        nick = self.s.nickname
        if names is None:
            self.lbl_acc.configure(text="Файл аккаунтов Freesm пока не найден. Откройте Freesm и добавьте аккаунт.",
                                   text_color=WARN)
        elif self.s.account_mode == "microsoft":
            self.lbl_acc.configure(text=f"В Freesm найдено аккаунтов: {len(names)}.",
                                   text_color=OK if names else WARN)
        elif nick and nick.lower() in (n.lower() for n in names):
            self.lbl_acc.configure(text=f"Аккаунт «{nick}» найден в Freesm.", text_color=OK)
        else:
            self.lbl_acc.configure(text=f"Аккаунта «{nick}» в Freesm нет. Есть: {', '.join(names) or 'пусто'}.",
                                   text_color=ERR)

    # ---------------- фон: проверка сборки ----------------
    def _startup_tasks(self) -> None:
        upd = core.check_launcher_update(self.s)
        if upd:
            self.q.put(("launcher_update", upd))
        self.q.put(("javas", javafind.find_javas()))
        self._refresh_manifest()

    def _refresh_manifest(self) -> None:
        if self.s.pack_repo.startswith("OWNER/"):
            self.q.put(("no_repo", None))
            return
        try:
            m = core.fetch_manifest(self.s)
            self.q.put(("manifest", m))
        except core.CoreError as e:
            self.q.put(("manifest_err", str(e)))

    def _apply_manifest(self, m: Manifest) -> None:
        self.manifest = m
        st = core.status(self.s, m)
        inst, latest = st["installed_version"], m.version
        self.lbl_sub.configure(text=m.server_name or m.name)
        self.lbl_detail.configure(text=f"Minecraft {m.minecraft} · {m.loader_type} {m.loader_version}")
        folder = self.s.play_kind == "folder"
        self.first_install = not (st["freesm_installed"] and st["instance_exists"] and inst)
        show_update = (lambda: self.btn_update.grid_remove()) if folder else (lambda: self.btn_update.grid())
        if self.first_install:
            self.lbl_status.configure(text="Сборка не установлена", text_color=WARN)
            self.btn_main.configure(text="Установить в папку" if folder else "Установить сборку")
            self.btn_update.grid_remove()
        elif version_tuple(inst) < version_tuple(latest):
            self.lbl_status.configure(text=f"Доступно обновление {latest}", text_color=WARN)
            self.lbl_detail.configure(text=f"Установлена {inst}. Minecraft {m.minecraft} · {m.loader_type} {m.loader_version}")
            self.btn_main.configure(text="Обновить сборку" if folder else "Обновить и играть")
            show_update()
        else:
            self.lbl_status.configure(text=f"Сборка {inst} актуальна", text_color=OK)
            self.btn_main.configure(text="Проверить файлы" if folder else "Играть")
            show_update()
        self.btn_main.configure(state="normal")
        self.lbl_news.configure(text=f"Что нового в {latest}")
        self._set_text(self.txt_news, "\n".join(m.changelog) if m.changelog else "Автор не оставил заметок к этой версии.")

    # ---------------- действия ----------------
    def _on_main(self) -> None:
        if self.manifest is None:
            self.btn_main.configure(state="disabled", text="Проверяю…")
            threading.Thread(target=self._refresh_manifest, daemon=True).start()
            return
        self._start(play=not self.first_install and self.s.play_kind != "folder")

    def _on_cancel(self) -> None:
        self.cancel.set()
        self.lbl_prog.configure(text="Отменяю…")

    def _start(self, play: bool) -> None:
        if self.busy:
            return
        self._collect()
        cfg.save(self.s)
        self.busy = True
        self.cancel = threading.Event()
        self.angle = 0.0
        self.btn_main.configure(state="disabled")
        self.btn_update.configure(state="disabled")
        self.btn_cancel.grid(row=9, column=0, pady=(10, 0))
        self.bar.set(0)
        self.tabs.set("Журнал")
        self._show_page("Журнал")
        self._tick_gears()
        threading.Thread(target=self._worker, args=(play,), daemon=True).start()

    def _worker(self, play: bool) -> None:
        was_first = self.first_install
        try:
            res = core.run_update(self.s, self.log, lambda d, t, n: self.q.put(("progress", d, t, n)),
                                  cancel=self.cancel, play=play, manifest=self.manifest)
            self.q.put(("done", res, was_first))
        except net.Cancelled:
            self.q.put(("cancelled",))
        except core.CoreError as e:
            self.log(f"✗ {e}")
            self.q.put(("error", str(e)))
        except Exception as e:  # непредвиденное: пишем в журнал и показываем текст
            self.log_file.exception("unexpected")
            self.q.put(("error", f"Непредвиденная ошибка: {e}"))

    def _finish(self) -> None:
        self.busy = False
        self.btn_cancel.grid_remove()
        self.btn_main.configure(state="normal")
        self.btn_update.configure(state="normal")
        self._draw_gears()
        threading.Thread(target=self._refresh_manifest, daemon=True).start()

    # ---------------- очередь сообщений ----------------
    def _poll(self) -> None:
        try:
            for _ in range(60):
                self._handle(self.q.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _handle(self, msg: tuple) -> None:
        kind = msg[0]
        if kind == "log":
            self._append_log(msg[1])
        elif kind == "progress":
            _k, done, total, name = msg
            self.bar.set(min(done / total, 1.0) if total else 0)
            if total:
                self.lbl_prog.configure(text=f"{name} · {done / 1048576:.0f} из {total / 1048576:.0f} МБ")
        elif kind == "manifest":
            self._apply_manifest(msg[1])
        elif kind == "manifest_err":
            self.manifest = None
            self.lbl_status.configure(text="Нет связи со сборкой", text_color=ERR)
            self.lbl_detail.configure(text=msg[1])
            self.btn_main.configure(text="Проверить снова", state="normal")
        elif kind == "no_repo":
            self.lbl_status.configure(text="Не указан репозиторий сборки", text_color=WARN)
            self.lbl_detail.configure(text="Откройте «Настройки» и впишите владелец/репозиторий.")
            self.btn_main.configure(text="Проверить снова", state="normal")
        elif kind == "javas":
            self.javas = msg[1]
            self._set_java_menu()
        elif kind == "launcher_update":
            tag, url = msg[1]
            self.lbl_update.configure(text=f"Вышла новая версия лаунчера {tag}. Скачать")
            self.lbl_update.bind("<Button-1>", lambda _e: webbrowser.open(url))
        elif kind == "done":
            res, was_first = msg[1], msg[2]
            self.bar.set(1 if res.ok else 0)
            self.lbl_prog.configure(text=res.summary())
            self._finish()
            if was_first and res.ok and self.s.play_kind == "folder":
                m = self.manifest
                messagebox.showinfo(
                    "Файлы сборки на месте",
                    "Запустите игру из вашего лаунчера. В профиле должны стоять Minecraft "
                    f"{m.minecraft if m else ''} и {(m.loader_type + ' ' + m.loader_version) if m else ''}.")
            elif was_first and res.ok:
                messagebox.showinfo(
                    "Сборка установлена",
                    "Осталось один раз добавить аккаунт в Freesm: вкладка «Игрок» → «Открыть Freesm» → "
                    "Аккаунты → Добавить оффлайн с вашим ником. Потом нажмите «Играть».")
            elif not res.ok:
                messagebox.showwarning("Есть ошибки", "Часть файлов не обновилась. Подробности на вкладке «Журнал».")
        elif kind == "cancelled":
            self.lbl_prog.configure(text="Отменено")
            self._finish()
        elif kind == "error":
            self.lbl_prog.configure(text="Ошибка")
            self._finish()
            messagebox.showerror("Не получилось", msg[1])


def _open_path(p: Path) -> None:
    try:
        if sys.platform.startswith("win"):
            import os
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", str(p)])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(p)])
    except OSError:
        pass


def run() -> None:
    App().mainloop()
