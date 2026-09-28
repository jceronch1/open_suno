#!/usr/bin/env bash
# Open Suno - arranque (Linux / macOS). Si aún no está instalado, ejecuta antes la instalación.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    echo "[Open Suno] Primera ejecución: instalando..."
    ./install.sh --yes
fi
exec .venv/bin/python run.py "$@"
