# -*- coding: utf-8 -*-
"""Копирует библиотеки Tcl/Tk на диск — иначе PyInstaller не может их упаковать.

Нужно один раз перед сборкой GUI-версии:
    python bundle_tcl.py
Затем:
    python -m PyInstaller ... --add-data "build_tcl\\tcl_library;_tcl_data" ...
"""

import os
import shutil
import sys
import tkinter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "build_tcl")


def copy_tree(tcl_interp, source, name):
    target = os.path.join(OUT, name)
    if os.path.isdir(target):
        shutil.rmtree(target)
    os.makedirs(OUT, exist_ok=True)

    # file copy сам рекурсивно копирует каталоги из виртуальной ФС Tcl
    tcl_interp.eval('file copy -force {%s} {%s}'
                    % (source, target.replace(os.sep, "/")))
    return target


def main():
    tcl = tkinter.Tcl()
    tcl_dir = tcl.eval("info library")
    print("Tcl:", tcl_dir)
    copy_tree(tcl, tcl_dir, "tcl_library")

    root = tkinter.Tk()
    root.withdraw()
    tk_dir = root.tk.eval("set tk_library")
    print("Tk: ", tk_dir)
    copy_tree(root.tk, tk_dir, "tk_library")
    root.destroy()

    ok = True
    for rel in ("tcl_library/init.tcl", "tk_library/tk.tcl"):
        path = os.path.join(OUT, rel.replace("/", os.sep))
        exists = os.path.exists(path)
        ok = ok and exists
        print(("OK  " if exists else "НЕТ ") + rel)

    total = sum(len(files) for _, _, files in os.walk(OUT))
    print(f"\nфайлов скопировано: {total}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
