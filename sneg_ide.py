#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sneg IDE — редактор кода с кнопкой «Запустить».

Запуск:
    python sneg_ide.py            # пустой редактор с примером
    python sneg_ide.py файл.sneg  # открыть файл

Горячие клавиши:
    F5           запустить
    Shift+F5     остановить
    Ctrl+S       сохранить
    Ctrl+O       открыть
    Ctrl+N       новый файл
"""

import os
import queue
import re
import sys
import threading
import time
import traceback

# В собранном exe пути к Tcl/Tk лежат рядом с программой — сообщаем об этом
# до импорта tkinter, иначе он падает с «Can't find a usable init.tcl».
if getattr(sys, "frozen", False):
    _base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    for _var, _folder in (("TCL_LIBRARY", "_tcl_data"), ("TK_LIBRARY", "_tk_data")):
        _path = os.path.join(_base, _folder)
        if os.path.isdir(_path):
            os.environ.setdefault(_var, _path)

import tkinter as tk
from tkinter import filedialog, messagebox

import sneg as lang

# ===========================================================================
#  ОФОРМЛЕНИЕ
# ===========================================================================

BG = "#14111B"
PANEL = "#1C1826"
PANEL_2 = "#241E31"
TEXT = "#E9E3F4"
MUTED = "#A99FC0"
LINE = "#322B42"
ACCENT = "#C68BE2"
GREEN = "#9ED97F"
ORANGE = "#F2A96B"
TEAL = "#7FD3C8"
RED = "#EF8585"

EDITOR_FONT = ("Consolas", 12)
CONSOLE_FONT = ("Consolas", 10)
UI_FONT = ("Segoe UI", 9)

WELCOME = '''# Добро пожаловать в sneg IDE
# Меняй код и жми ▶ (F5)

let hp = 100
let имя = "sneg"

print("привет,", имя)
print("здоровье:", hp)

fn урон(сила, крит) {
    if крит { return сила * 2 }
    return сила
}

let сила = randint(8, 15)
hp -= урон(сила, chance(25))

print("после удара:", hp, "hp")
'''


def enable_dpi_awareness():
    """Чтобы шрифты не мылились на мониторах с масштабом."""
    if not sys.platform.startswith("win"):
        return
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


# ===========================================================================
#  МОСТИК МЕЖДУ ПОТОКОМ ПРОГРАММЫ И ОКНОМ
# ===========================================================================

class OutputWriter:
    """Всё, что программа печатает, уходит в очередь, а не в консоль."""

    def __init__(self, put):
        self.put = put

    def write(self, text):
        self.put(("out", text))

    def flush(self):
        pass


class InputReader:
    """input() ждёт, пока пользователь введёт строку в окне."""

    def __init__(self, put, take):
        self.put = put
        self.take = take

    def readline(self):
        self.put(("input", None))
        line = self.take()
        if line is None:
            return ""
        return line + "\n"


class StoppableInterpreter(lang.Interpreter):
    """Проверяет флажок остановки перед каждым оператором."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stop_requested = False

    def exec_stmt(self, node, env):
        if self.stop_requested:
            raise lang.SnegError("остановлено пользователем")
        return super().exec_stmt(node, env)


# ===========================================================================
#  ПРИЛОЖЕНИЕ
# ===========================================================================

TOKEN_RE = re.compile(
    r"(?P<com>#[^\n]*|//[^\n]*|/\*[\s\S]*?\*/)"
    r'|(?P<str>"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')'
    r"|(?P<num>\b\d[\d_]*(?:\.\d+)?\b)"
    r"|(?P<word>[A-Za-zА-Яа-яЁё_]\w*)"
)

WORD_KINDS = set(lang.KEYWORDS.keys())


class IdeApp:
    def __init__(self, root, filename=None):
        self.root = root
        self.path = None
        self.dirty = False
        self.queue = queue.Queue()
        self.machine = None
        self.running = False
        self.input_queue = None
        self.started_at = 0.0
        self.highlight_job = None

        self.build_ui()
        self.bind_keys()

        if filename and os.path.exists(filename):
            self.open_path(filename)
        else:
            self.editor.insert("1.0", WELCOME)

        self.refresh_all()
        # стартовый текст не считается изменением — иначе сразу «●» в заголовке
        self.editor.edit_modified(False)
        self.dirty = False
        self.update_title()
        self.root.after(60, self.pump)

    # --- сборка окна ------------------------------------------------------
    def build_ui(self):
        root = self.root
        root.title("sneg IDE")

        # окно не должно вылезать за экран — на ноутбуках и при масштабе 125% это легко
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        win_w = min(1040, max(640, screen_w - 80))
        win_h = min(720, max(420, screen_h - 120))
        root.geometry(f"{win_w}x{win_h}+{max(0, (screen_w - win_w) // 2)}+{max(0, (screen_h - win_h) // 3)}")
        root.minsize(640, 380)
        root.configure(bg=BG)

        # панель инструментов
        bar = tk.Frame(root, bg=PANEL, height=42)
        bar.pack(side="top", fill="x")

        def button(text, command, fg=TEXT):
            btn = tk.Button(
                bar, text=text, command=command, font=UI_FONT,
                bg=PANEL, fg=fg, activebackground=PANEL_2, activeforeground=TEXT,
                relief="flat", bd=0, padx=12, pady=6, cursor="hand2",
                highlightthickness=0,
            )
            btn.pack(side="left", padx=(6, 0), pady=6)
            return btn

        button("Новый", self.new_file)
        button("Открыть", self.open_file)
        button("Сохранить", self.save_file)

        tk.Frame(bar, bg=LINE, width=1, height=24).pack(side="left", padx=10, pady=9)

        self.btn_run = button("▶  Запустить", self.run_code, fg=GREEN)
        self.btn_stop = button("■  Стоп", self.stop_code, fg=MUTED)
        button("Очистить", self.clear_console, fg=MUTED)

        self.title_label = tk.Label(
            bar, text="", font=UI_FONT, bg=PANEL, fg=MUTED, anchor="e")
        self.title_label.pack(side="right", padx=14)

        # строку состояния пакуем ДО растягиваемой панели, иначе останется 1 пиксель
        self.status = tk.Label(root, text="", font=UI_FONT, bg=PANEL_2, fg=MUTED,
                               anchor="w", padx=12, pady=4)
        self.status.pack(side="bottom", fill="x")

        # рабочая область: редактор сверху, вывод снизу
        paned = tk.PanedWindow(
            root, orient="vertical", bg=LINE, sashwidth=6,
            sashrelief="flat", bd=0, sashpad=0)
        paned.pack(fill="both", expand=True, padx=0, pady=0)

        # --- редактор ---
        editor_frame = tk.Frame(paned, bg=BG)
        paned.add(editor_frame, minsize=140, stretch="always")

        self.linenums = tk.Text(
            editor_frame, width=4, padx=6, pady=8, takefocus=0, bd=0,
            bg=BG, fg="#5B5470", font=EDITOR_FONT, state="disabled",
            highlightthickness=0, cursor="arrow")
        self.linenums.pack(side="left", fill="y")

        self.vbar = tk.Scrollbar(editor_frame, orient="vertical", width=12,
                                 troughcolor=BG, bg=PANEL, bd=0,
                                 activebackground=LINE)
        self.vbar.pack(side="right", fill="y")

        self.editor = tk.Text(
            editor_frame, wrap="none", undo=True, bd=0,
            bg=BG, fg=TEXT, insertbackground=ACCENT, insertwidth=2,
            selectbackground="#3B2F53", selectforeground=TEXT,
            font=EDITOR_FONT, padx=10, pady=8, highlightthickness=0,
            yscrollcommand=self.on_editor_scroll, tabs=("4c",))
        self.editor.pack(side="left", fill="both", expand=True)
        self.vbar.configure(command=self.yview_both)

        self.editor.tag_configure("kw", foreground=ACCENT)
        self.editor.tag_configure("str", foreground=GREEN)
        self.editor.tag_configure("num", foreground=ORANGE)
        self.editor.tag_configure("com", foreground="#7C7396")
        self.editor.tag_configure("type", foreground=TEAL)
        self.editor.tag_configure("err", foreground=RED)

        # --- консоль ---
        console_frame = tk.Frame(paned, bg=PANEL)
        paned.add(console_frame, minsize=110)

        head = tk.Frame(console_frame, bg=PANEL)
        head.pack(side="top", fill="x")
        tk.Label(head, text="ВЫВОД", font=UI_FONT, bg=PANEL, fg=MUTED).pack(
            side="left", padx=12, pady=(8, 4))

        self.console = tk.Text(
            console_frame, height=9, wrap="word", bd=0,
            bg=PANEL, fg=TEXT, font=CONSOLE_FONT, padx=12, pady=6,
            highlightthickness=0, state="disabled")
        self.console.pack(side="top", fill="both", expand=True)
        self.console.tag_configure("dim", foreground=MUTED)
        self.console.tag_configure("ok", foreground=GREEN)
        self.console.tag_configure("err", foreground=RED)
        self.console.tag_configure("inp", foreground=TEAL)

        input_row = tk.Frame(console_frame, bg=PANEL_2)
        input_row.pack(side="bottom", fill="x")
        self.input_label = tk.Label(input_row, text="Ввод →", font=UI_FONT,
                                    bg=PANEL_2, fg=MUTED)
        self.input_label.pack(side="left", padx=(12, 6), pady=6)
        self.input_entry = tk.Entry(
            input_row, font=CONSOLE_FONT, bg=PANEL_2, fg=TEXT,
            insertbackground=ACCENT, relief="flat", highlightthickness=0,
            state="disabled", disabledbackground=PANEL_2,
            disabledforeground="#5B5470")
        self.input_entry.pack(side="left", fill="x", expand=True, padx=(0, 12), pady=6)
        self.input_entry.bind("<Return>", self.send_input)

        # строка состояния уже упакована выше — см. build_ui

    def bind_keys(self):
        self.root.bind("<F5>", lambda e: self.run_code())
        self.root.bind("<Shift-F5>", lambda e: self.stop_code())
        self.root.bind("<Control-s>", lambda e: self.save_file())
        self.root.bind("<Control-o>", lambda e: self.open_file())
        self.root.bind("<Control-n>", lambda e: self.new_file())
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.editor.bind("<<Modified>>", self.on_modified)
        self.editor.bind("<KeyRelease>", self.update_cursor)
        self.editor.bind("<ButtonRelease-1>", self.update_cursor)
        self.editor.bind("<Tab>", self.insert_tab)

    # --- прокрутка --------------------------------------------------------
    def on_editor_scroll(self, first, last):
        self.vbar.set(first, last)
        self.linenums.yview_moveto(first)

    def yview_both(self, *args):
        self.editor.yview(*args)
        self.linenums.yview(*args)

    def insert_tab(self, event=None):
        self.editor.insert("insert", "    ")
        return "break"

    # --- подсветка и номера строк ----------------------------------------
    def on_modified(self, event=None):
        if not self.editor.edit_modified():
            return
        self.editor.edit_modified(False)
        self.mark_dirty(True)
        self.update_linenums()
        if self.highlight_job:
            self.root.after_cancel(self.highlight_job)
        self.highlight_job = self.root.after(120, self.apply_highlight)

    def update_linenums(self):
        count = int(self.editor.index("end-1c").split(".")[0])
        numbers = "\n".join(str(i) for i in range(1, count + 1))

        self.linenums.configure(state="normal")
        self.linenums.delete("1.0", "end")
        self.linenums.insert("1.0", numbers)
        self.linenums.configure(state="disabled")
        self.linenums.yview_moveto(self.editor.yview()[0])

    def apply_highlight(self):
        text = self.editor.get("1.0", "end-1c")

        for tag in ("kw", "str", "num", "com", "type"):
            self.editor.tag_remove(tag, "1.0", "end")

        for match in TOKEN_RE.finditer(text):
            kind = match.lastgroup
            word = match.group()

            if kind == "word":
                if word in WORD_KINDS:
                    tag = "kw"
                elif word[:1].isupper():
                    tag = "type"
                else:
                    continue
            else:
                tag = {"com": "com", "str": "str", "num": "num"}[kind]

            start = "1.0 + %dc" % match.start()
            end = "1.0 + %dc" % match.end()
            self.editor.tag_add(tag, start, end)

    def update_cursor(self, event=None):
        line, col = self.editor.index("insert").split(".")
        state = "выполняется" if self.running else "готов"
        self.status.configure(text=f"стр {line}, кол {int(col) + 1}   ·   {state}   ·   sneg {lang.VERSION}")

    # --- файлы ------------------------------------------------------------
    def current_name(self):
        return os.path.basename(self.path) if self.path else "без имени"

    def refresh_all(self):
        self.update_linenums()
        self.apply_highlight()
        self.update_cursor()
        self.update_title()

    def update_title(self):
        mark = "● " if self.dirty else ""
        name = self.current_name()
        self.root.title(f"{mark}{name} — sneg IDE")
        self.title_label.configure(text=name)

    def mark_dirty(self, value):
        if self.dirty != value:
            self.dirty = value
            self.update_title()

    def new_file(self):
        if not self.confirm_discard():
            return
        self.editor.delete("1.0", "end")
        self.path = None
        self.mark_dirty(False)
        self.clear_console()
        self.refresh_all()

    def open_file(self):
        if not self.confirm_discard():
            return
        path = filedialog.askopenfilename(
            title="Открыть программу",
            filetypes=[("sneg", "*.sneg"), ("Все файлы", "*.*")])
        if path:
            self.open_path(path)

    def open_path(self, path):
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as err:
            messagebox.showerror("sneg IDE", f"Не смог открыть файл:\n{err}")
            return

        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", text)
        self.path = path
        self.refresh_all()
        self.editor.edit_modified(False)
        self.mark_dirty(False)

    def save_file(self):
        if not self.path:
            return self.save_file_as()

        try:
            with open(self.path, "w", encoding="utf-8") as handle:
                handle.write(self.editor.get("1.0", "end-1c"))
        except OSError as err:
            messagebox.showerror("sneg IDE", f"Не смог сохранить:\n{err}")
            return
        self.mark_dirty(False)

    def save_file_as(self):
        path = filedialog.asksaveasfilename(
            title="Сохранить как",
            defaultextension=".sneg",
            filetypes=[("sneg", "*.sneg"), ("Все файлы", "*.*")])
        if not path:
            return
        self.path = path
        self.save_file()

    def confirm_discard(self):
        if not self.dirty:
            return True
        answer = messagebox.askyesnocancel(
            "sneg IDE", f"Сохранить изменения в {self.current_name()}?")
        if answer is None:
            return False
        if answer:
            self.save_file()
            return not self.dirty
        return True

    def on_close(self):
        if self.running:
            if not messagebox.askyesno("sneg IDE", "Программа ещё выполняется. Выйти?"):
                return
        if self.confirm_discard():
            self.root.destroy()

    # --- консоль ----------------------------------------------------------
    def write_console(self, text, tag=None):
        self.console.configure(state="normal")
        self.console.insert("end", text, tag or ())
        self.console.see("end")
        self.console.configure(state="disabled")

    def clear_console(self):
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    def set_running_ui(self, running):
        self.running = running
        self.btn_run.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(
            state="normal" if running else "disabled",
            fg=RED if running else MUTED)
        self.update_cursor()

    # --- запуск -----------------------------------------------------------
    def run_code(self):
        if self.running:
            return

        source = self.editor.get("1.0", "end-1c")
        if not source.strip():
            return

        self.clear_console()
        self.write_console(f"▶ {self.current_name()}\n", "dim")

        self.input_queue = queue.Queue()
        self.started_at = time.time()
        self.set_running_ui(True)

        thread = threading.Thread(target=self.execute, args=(source,), daemon=True)
        thread.start()

    def execute(self, source):
        put = self.queue.put

        out = OutputWriter(lambda item: put(item))
        stdin = InputReader(lambda item: put(item), self.input_queue.get)

        machine = StoppableInterpreter(out, stdin)
        self.machine = machine
        name = self.current_name()

        try:
            program, _ = lang.parse_source(source, name)
            machine.execute(program)
            put(("done", None))

        except lang.SnegError as err:
            put(("error", (err, source)))

        except SystemExit:
            put(("done", None))

        except RecursionError:
            put(("error", (lang.SnegError("слишком глубокая рекурсия"), source)))

        except BaseException:
            text = traceback.format_exc()
            put(("error", (lang.SnegError("внутренняя ошибка:\n" + text), source)))

    def stop_code(self):
        if self.machine is not None:
            self.machine.stop_requested = True
            self.write_console("\n⏹ просим программу остановиться…\n", "dim")

    def finish_run(self, ok):
        if self.machine is not None:
            self.machine = None
        self.set_running_ui(False)

        elapsed = time.time() - self.started_at
        if ok:
            self.write_console(f"\n✓ готово за {elapsed:.2f} с\n", "ok")

    def show_error(self, err, source):
        text = lang.format_error(err, source.splitlines(), self.current_name())
        self.write_console(text + "\n", "err")
        self.finish_run(False)

    # --- ввод -------------------------------------------------------------
    def ask_input(self):
        self.input_entry.configure(state="normal")
        self.input_entry.focus_set()
        self.write_console("> ", "inp")

    def send_input(self, event=None):
        text = self.input_entry.get()
        self.input_entry.delete(0, "end")
        self.input_entry.configure(state="disabled")
        self.editor.focus_set()

        if self.input_queue is not None:
            self.input_queue.put(text)

    # --- очередь событий --------------------------------------------------
    def pump(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()

                if kind == "out":
                    self.write_console(payload)
                elif kind == "input":
                    self.ask_input()
                elif kind == "done":
                    self.finish_run(True)
                elif kind == "error":
                    self.show_error(*payload)

        except queue.Empty:
            pass

        self.root.after(60, self.pump)


def main():
    args = sys.argv[1:]
    selftest = "--selftest" in args
    args = [a for a in args if a != "--selftest"]

    enable_dpi_awareness()

    root = None
    try:
        root = tk.Tk()
        IdeApp(root, args[0] if args else None)

        if selftest:
            root.after(500, root.destroy)

        root.mainloop()

    except BaseException:
        crash = traceback.format_exc()
        lang.log_event("КРАШ IDE:\n" + crash)
        try:
            messagebox.showerror("sneg IDE", "Что-то сломалось:\n\n" + crash[-1500:])
        except Exception:
            pass
        raise

    if selftest:
        print("ui ok")


if __name__ == "__main__":
    main()
