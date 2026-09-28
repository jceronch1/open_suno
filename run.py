"""Arranque de Open Suno: python run.py [--host 0.0.0.0] [--port 7870] [--no-browser] [--allow-remote-admin]"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser

import httpx
import uvicorn

from app import __version__
from app.settings import SettingsStore


def _already_running(url: str) -> bool:
    try:
        return httpx.get(url + "/api/state", timeout=1.5).json().get("version") is not None
    except (httpx.HTTPError, ValueError):
        return False


def main() -> None:
    cfg = SettingsStore().get()["app"]
    ap = argparse.ArgumentParser(description="Open Suno: crea música con ACE-Step 1.5 en tu CPU o GPU")
    ap.add_argument("--host", default=cfg["host"], help="dirección de escucha (0.0.0.0 para abrirla en la red local)")
    ap.add_argument("--port", type=int, default=cfg["port"])
    ap.add_argument("--no-browser", action="store_true", help="no abrir el navegador al arrancar")
    ap.add_argument(
        "--allow-remote-admin",
        action="store_true",
        help="permitir desde otros equipos cambiar el motor, descargar o borrar modelos",
    )
    args = ap.parse_args()

    for stream in (sys.stdout, sys.stderr):  # consolas cp1252 de Windows
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"
    open_browser = cfg["open_browser"] and not args.no_browser

    if _already_running(url):  # doble clic con la app ya abierta: sólo mostrarla
        print(f"\n  Open Suno ya está en marcha en {url}\n", flush=True)
        if open_browser:
            webbrowser.open(url)
        return

    if args.allow_remote_admin:
        os.environ["OPENSUNO_ALLOW_REMOTE_ADMIN"] = "1"
    print(f"\n  Open Suno {__version__} -> {url}", flush=True)
    if args.host in ("0.0.0.0", "::"):
        print("  Accesible desde la red local: la API no tiene contraseña, úsalo sólo en redes de confianza.")
    print("  (Ctrl+C o cierra esta ventana para salir)\n", flush=True)
    if open_browser:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
