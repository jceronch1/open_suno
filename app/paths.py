"""Rutas del proyecto. Todo lo relativo se resuelve contra la raíz de Open Suno."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# OPENSUNO_DATA permite usar otra carpeta de datos (p. ej. una biblioteca de demostración).
DATA = Path(os.environ["OPENSUNO_DATA"]).resolve() if os.environ.get("OPENSUNO_DATA") else ROOT / "data"
LIBRARY = DATA / "library"
UPLOADS = DATA / "uploads"
WEB = ROOT / "web"

IS_WINDOWS = os.name == "nt"
EXE = ".exe" if IS_WINDOWS else ""


def resolve(p: str | os.PathLike) -> Path:
    """Convierte una ruta de configuración (relativa a ROOT o absoluta) en absoluta."""
    path = Path(p).expanduser()
    return path if path.is_absolute() else (ROOT / path)


def ensure_dirs() -> None:
    for d in (DATA, LIBRARY, UPLOADS):
        d.mkdir(parents=True, exist_ok=True)
