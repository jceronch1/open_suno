"""Instalador de Open Suno.

Descarga (o compila) el motor acestep.cpp, descarga los modelos GGUF de ACE-Step 1.5,
detecta CPU/GPU y deja configurado el mejor perfil. Lo ejecutan install.bat / install.sh.

    python -m app.installer                 instalación completa (pregunta lo opcional)
    python -m app.installer --yes           sin preguntas
    python -m app.installer --cuda          instala también el runtime CUDA (Windows + NVIDIA)
    python -m app.installer --profile cpu   fuerza el perfil (auto | cpu | gpu | gpu_low | gpu_high)
    python -m app.installer --update-engine vuelve a descargar/compilar el motor
    python -m app.installer --models acestep-5Hz-lm-1.7B-Q8_0.gguf,acestep-v15-sft-Q4_K_M.gguf
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

from .downloads import RECOMMENDED, DownloadManager
from .hardware import best_gpu, nvidia_gpus, probe_devices, save_cached_devices
from .paths import EXE, IS_WINDOWS, ROOT, ensure_dirs, resolve
from .profiles import apply_profile
from .settings import PROFILES, SettingsStore


def _out(msg: str = "") -> None:
    print(msg, flush=True)


def _fmt(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} GB"


def _bar(frac: float, width: int = 28) -> str:
    frac = max(0.0, min(frac, 1.0))
    full = int(frac * width)
    return "#" * full + "-" * (width - full)


def _run_task(dm: DownloadManager, task: dict) -> None:
    """Espera a una descarga mostrando una barra de progreso en la consola."""
    label = task["label"]
    try:
        while True:
            t = next((x for x in dm.list() if x["id"] == task["id"]), task)
            if t["status"] in ("done", "failed", "cancelled"):
                break
            total, done = t.get("total") or 0, t.get("done") or 0
            frac = done / total if total else 0
            extra = f" {_fmt(t['speed'])}/s" if t.get("speed") else ""
            cur = t.get("current_file") or ""
            if cur.startswith("Verificando") or cur.startswith("Extrayendo") or cur.startswith("Instalando"):
                extra = f" {cur}"
            sys.stdout.write(f"\r  [{_bar(frac)}] {frac * 100:5.1f}%  {_fmt(done)} / {_fmt(total)}{extra}".ljust(100))
            sys.stdout.flush()
            time.sleep(0.4)
    except KeyboardInterrupt:
        dm.cancel(task["id"])
        _out("\n  Cancelado. Puedes volver a ejecutar el instalador: las descargas se reanudan.")
        raise SystemExit(1) from None
    sys.stdout.write("\r" + " " * 100 + "\r")
    if t["status"] != "done":
        _out(f"  [ERROR] {label}: {t.get('error') or t['status']}")
        raise SystemExit(1)
    _out(f"  [OK] {label}")


def _ask(question: str, default: bool, assume_yes: bool) -> bool:
    if assume_yes or not sys.stdin.isatty():
        return default
    hint = "[S/n]" if default else "[s/N]"
    try:
        ans = input(f"{question} {hint} ").strip().lower()
    except EOFError:
        return default
    return default if not ans else ans in ("s", "si", "sí", "y", "yes")


# ------------------------------------------------------------------ motor
def _build_from_source(settings: SettingsStore, backend: str) -> bool:
    """Compila acestep.cpp con el script de la plataforma y apunta bin_dir al resultado."""
    if IS_WINDOWS:
        script = ROOT / "scripts" / "build_engine.ps1"
        cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Backend", backend]
        out_dir = "engine/src/acestep.cpp/build/Release"
    else:
        script = ROOT / "scripts" / "build_engine.sh"
        cmd = ["bash", str(script), backend]
        out_dir = "engine/src/acestep.cpp/build"
    _out(f"  Compilando acestep.cpp ({backend}); puede tardar 5-20 minutos…")
    if subprocess.run(cmd, cwd=str(ROOT)).returncode != 0:
        return False
    if not (resolve(out_dir) / f"ace-server{EXE}").exists():
        return False
    settings.update({"engine": {"bin_dir": out_dir}})
    return True


def install_engine(settings: SettingsStore, dm: DownloadManager, args) -> None:
    exe = resolve(settings.engine["bin_dir"]) / f"ace-server{EXE}"
    if exe.exists() and not args.update_engine and not args.build:
        _out(f"  [OK] Motor ya instalado ({exe.parent})")
        return
    if IS_WINDOWS and not args.build:
        _out("  Descargando binarios de acestep.cpp para Windows (CPU + CUDA + Vulkan)…")
        try:
            _run_task(dm, dm.add_engine())
            return
        except SystemExit:
            _out("  No se pudieron descargar los binarios precompilados; se intentará compilar el motor.")
    if not _build_from_source(settings, args.backend):
        _out("  [ERROR] No se pudo instalar el motor.")
        if IS_WINDOWS:
            _out("  Instala Visual Studio Build Tools (C++) y CMake, o vuelve a intentarlo más tarde.")
        else:
            _out("  Necesitas git, cmake y un compilador C++ (y CUDA Toolkit o Vulkan SDK para GPU).")
        raise SystemExit(1)
    _out("  [OK] Motor compilado")


# --------------------------------------------------------------- modelos
def install_models(settings: SettingsStore, dm: DownloadManager, extra: list[str]) -> None:
    cat = dm.catalog(refresh=True)
    installed = {m["name"] for m in cat["models"] if m["installed"]}
    known = {m["name"]: m for m in cat["models"]}
    wanted = list(dict.fromkeys(RECOMMENDED + extra))
    pending = [n for n in wanted if n not in installed]
    total = sum((known.get(n, {}).get("size") or 0) for n in pending)
    if not pending:
        _out("  [OK] Modelos ya instalados")
        return
    _out(f"  {len(pending)} modelo(s) por descargar, {_fmt(total)} en total (Hugging Face: Serveurperso/ACE-Step-1.5-GGUF)")
    for name in pending:
        if name not in known:
            _out(f"  [AVISO] {name} no existe en el repositorio de modelos; se omite.")
            continue
        _out(f"  · {name} ({_fmt(known[name].get('size') or 0)})")
        _run_task(dm, dm.add_model(name))


# ---------------------------------------------------------- dispositivos
def configure_device(settings: SettingsStore, profile: str) -> None:
    _out("  Detectando dispositivos que puede usar el motor…")
    data = probe_devices(settings.engine)
    if not data.get("ok"):
        _out(f"  [AVISO] {data.get('error')}. Se usará la CPU.")
        settings.update({"engine": {"device": "CPU", "profile": "custom"}})
        return
    save_cached_devices(data)
    devices = data["devices"]
    for d in devices:
        extra = " (integrada)" if d.get("integrated") else ""
        vram = f" · {d['vram_mb'] / 1024:.0f} GB" if d.get("vram_mb") else ""
        _out(f"    - {d['id']:<9} {d['name']}{extra}{vram}")
    gpu = best_gpu(devices)
    if profile == "auto":
        profile = "gpu" if gpu else "cpu"
        if gpu:
            vram = next((d.get("vram_mb") for d in devices if d["id"] == gpu), None)
            if vram and vram < 6000:
                profile = "gpu_low"
            elif vram and vram >= 12000:
                profile = "gpu_high"
    try:
        data = apply_profile(settings, profile, devices)
    except ValueError as e:
        _out(f"  [AVISO] {e} Se usará la CPU.")
        data = apply_profile(settings, "cpu", devices)
        profile = "cpu"
    e = data["engine"]
    _out(f"  [OK] Perfil «{PROFILES[profile]['label']}»: dispositivo {e['device']}, VAE chunk {e['vae_chunk']}")


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m app.installer", description="Instalador de Open Suno")
    ap.add_argument("--yes", "-y", action="store_true", help="no preguntar (usa las opciones por defecto)")
    ap.add_argument("--cuda", action="store_true", help="instalar el runtime CUDA (Windows + NVIDIA, ~430 MB)")
    ap.add_argument("--no-models", action="store_true", help="no descargar modelos")
    ap.add_argument("--models", default="", help="modelos GGUF adicionales separados por comas")
    ap.add_argument("--profile", default="auto", choices=["auto", *PROFILES], help="perfil de dispositivo")
    ap.add_argument("--update-engine", action="store_true", help="volver a descargar el motor")
    ap.add_argument("--build", action="store_true", help="compilar el motor desde el código fuente")
    ap.add_argument("--backend", default="auto", choices=["auto", "cuda", "vulkan", "cpu"], help="backend al compilar")
    ap.add_argument("--no-pause", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    ensure_dirs()
    settings = SettingsStore()
    dm = DownloadManager(settings)
    _out("\n=== Open Suno · instalación ===\n")

    _out("[1/4] Motor acestep.cpp")
    install_engine(settings, dm, args)

    _out("\n[2/4] Modelos ACE-Step 1.5")
    if args.no_models:
        _out("  Omitido (--no-models)")
    else:
        install_models(settings, dm, [m.strip() for m in args.models.split(",") if m.strip()])

    _out("\n[3/4] Runtime CUDA (opcional)")
    cat = dm.catalog()
    has_nvidia = IS_WINDOWS and bool(nvidia_gpus())
    if not has_nvidia:
        _out("  No aplica (sin GPU NVIDIA en Windows)")
    elif cat["cuda"]["installed"]:
        _out("  [OK] Ya instalado")
    elif args.cuda or _ask(
        "  Tienes GPU NVIDIA. Ya funciona por Vulkan; ¿instalar también el runtime CUDA (~430 MB)?", False, args.yes
    ):
        _run_task(dm, dm.add_cuda())
    else:
        _out("  Omitido: la GPU NVIDIA se usará por Vulkan. Puedes instalarlo luego en Modelos.")

    _out("\n[4/4] Configuración CPU/GPU")
    configure_device(settings, args.profile)

    port = settings.get()["app"]["port"]
    start = "start.bat" if IS_WINDOWS else "./start.sh"
    _out(f"\nListo. Arranca Open Suno con {start} y abre http://127.0.0.1:{port}\n")


if __name__ == "__main__":
    main()
