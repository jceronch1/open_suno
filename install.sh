#!/usr/bin/env bash
# Open Suno - instalación (Linux / macOS): entorno de Python, motor acestep.cpp, modelos y CPU/GPU.
set -e
cd "$(dirname "$0")"
echo
echo "  ==========  Open Suno  =========="
echo

PY=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
        PY="$cand"; break
    fi
done
if [ -z "$PY" ]; then
    echo "[ERROR] Necesitas Python 3.10 o superior (paquetes python3 y python3-venv)."
    exit 1
fi

if [ ! -x .venv/bin/python ]; then
    echo "[Open Suno] Creando entorno virtual de Python..."
    "$PY" -m venv .venv
fi
echo "[Open Suno] Instalando dependencias de Python..."
.venv/bin/python -m pip install --disable-pip-version-check -q --upgrade pip
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt

exec .venv/bin/python -m app.installer "$@"
