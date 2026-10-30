"""Ejecución de procesos auxiliares sin abrir ventanas en Windows.

El hub se ejecuta en una app de escritorio sin consola, pero cada
``subprocess`` lanzado por el auditor, el escáner o los helpers podía crear
una ventana ``conhost``/PowerShell. En Windows se aplica ``CREATE_NO_WINDOW``
y un ``STARTUPINFO`` oculto; en Linux/macOS no cambia nada.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any


def hidden_process_kwargs() -> dict[str, Any]:
    """Devuelve los kwargs de Windows que evitan una ventana de consola."""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def run_hidden(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """``subprocess.run`` con la ventana oculta cuando corre en Windows."""
    merged = hidden_process_kwargs()
    merged.update(kwargs)
    return subprocess.run(cmd, **merged)


__all__ = ["hidden_process_kwargs", "run_hidden"]
