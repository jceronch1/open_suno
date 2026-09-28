#!/usr/bin/env bash
# Descarga y compila acestep.cpp (Linux / macOS). Los binarios quedan en
# engine/src/acestep.cpp/build y el instalador apunta Open Suno a esa carpeta.
#   ./scripts/build_engine.sh [auto|cuda|vulkan|cpu]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/engine/src/acestep.cpp"
BACKEND="${1:-auto}"

for tool in git cmake; do
    command -v "$tool" >/dev/null 2>&1 || { echo "[ERROR] Falta $tool. Instálalo e inténtalo de nuevo."; exit 1; }
done

if [ -d "$SRC/.git" ]; then
    git -C "$SRC" pull --ff-only
    git -C "$SRC" submodule update --init --recursive
else
    mkdir -p "$(dirname "$SRC")"
    git clone --depth 1 --recurse-submodules https://github.com/ServeurpersoCom/acestep.cpp.git "$SRC"
fi

if [ "$BACKEND" = "auto" ]; then
    if [ "$(uname)" = "Darwin" ]; then BACKEND=cpu          # macOS: Metal + Accelerate se activan solos
    elif command -v nvcc >/dev/null 2>&1; then BACKEND=cuda
    elif command -v glslc >/dev/null 2>&1; then BACKEND=vulkan
    else BACKEND=cpu; fi
fi

cd "$SRC"
echo "[Open Suno] Compilando acestep.cpp con backend: $BACKEND"
case "$BACKEND" in
    cuda)   ./buildcuda.sh ;;
    vulkan) ./buildvulkan.sh ;;
    cpu)    ./buildcpu.sh ;;
    *) echo "[ERROR] Backend desconocido: $BACKEND"; exit 1 ;;
esac

test -x "$SRC/build/ace-server" || { echo "[ERROR] La compilación no generó ace-server"; exit 1; }
echo "[Open Suno] Motor listo en $SRC/build"
