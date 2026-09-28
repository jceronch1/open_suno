"""Detección de hardware y de los dispositivos de cómputo que ve el motor GGML."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import struct
import subprocess
import threading
import time
import wave
from pathlib import Path

import psutil

from .paths import DATA, EXE, IS_WINDOWS, resolve

DEVICES_CACHE = DATA / "devices.json"
_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def cpu_name() -> str:
    if IS_WINDOWS:
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            ) as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "CPU"


def nvidia_gpus() -> list[dict]:
    """Estado de las GPU NVIDIA vía nvidia-smi (vacío si no hay driver)."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run(
            [
                exe,
                "--query-gpu=index,name,memory.total,memory.used,utilization.gpu,driver_version,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=_NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 7:
            continue

        def num(v: str) -> float | None:
            try:
                return float(v)
            except ValueError:
                return None

        gpus.append(
            {
                "index": int(num(parts[0]) or 0),
                "name": parts[1],
                "memory_total_mb": num(parts[2]),
                "memory_used_mb": num(parts[3]),
                "utilization": num(parts[4]),
                "driver": parts[5],
                "temperature": num(parts[6]),
            }
        )
    return gpus


def system_info() -> dict:
    vm = psutil.virtual_memory()
    return {
        "os": f"{platform.system()} {platform.release()}",
        "cpu": {
            "name": cpu_name(),
            "cores": psutil.cpu_count(logical=False) or 0,
            "threads": psutil.cpu_count(logical=True) or 0,
            "usage": psutil.cpu_percent(interval=None),
        },
        "ram": {"total_gb": round(vm.total / 2**30, 1), "used_gb": round(vm.used / 2**30, 1)},
        "nvidia": nvidia_gpus(),
    }


# --------------------------------------------------------------------------
# Sondeo de dispositivos del motor
# --------------------------------------------------------------------------
# backend.h de acestep.cpp: si GGML_BACKEND apunta a un dispositivo que no
# existe, imprime "Available: CUDA0 Vulkan0 CPU" y sale. Lo aprovechamos para
# saber exactamente qué dispositivos puede usar el motor con los binarios y
# DLL instalados (sin adivinar a partir del hardware).

_RE_AVAILABLE = re.compile(r"Available:\s*(.+)$")
_RE_VK = re.compile(r"ggml_vulkan:\s*(\d+)\s*=\s*(.+?)\s*(?:\(([^)]*)\))?\s*\|\s*uma:\s*(\d)")
_RE_CUDA = re.compile(r"^\s*Device\s+(\d+):\s*([^,]+),\s*compute capability\s*([\d.]+)")
_RE_CPU_VARIANT = re.compile(r"loaded CPU backend from .*ggml-cpu-([\w-]+)\.\w+")

_probe_lock = threading.Lock()


def _tiny_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(struct.pack("<hh", 0, 0) * 4800)


_cuda_major_cache: dict[tuple[str, float, int], int | None] = {}


def required_cuda_major(bin_dir: Path) -> int | None:
    """Versión mayor de CUDA que pide ggml-cuda (busca 'cublas64_NN.dll' en el binario).

    ggml-cuda.dll pesa >100 MB y esto se consulta en cada sondeo de estado, así que se
    cachea por ruta, fecha y tamaño del archivo.
    """
    dll = bin_dir / "ggml-cuda.dll"
    try:
        st = dll.stat()
    except OSError:
        return None
    key = (str(dll), st.st_mtime, st.st_size)
    if key not in _cuda_major_cache:
        major = None
        try:
            with open(dll, "rb") as fh:
                tail = b""
                while chunk := fh.read(8 << 20):
                    buf = tail + chunk  # solapamiento: el nombre puede quedar partido entre bloques
                    if m := re.search(rb"cublas64_(\d+)\.dll", buf):
                        major = int(m.group(1))
                        break
                    tail = buf[-32:]
        except OSError:
            pass
        _cuda_major_cache.clear()
        _cuda_major_cache[key] = major
    return _cuda_major_cache[key]


def cuda_runtime_present(bin_dir: Path, cuda_dir: Path) -> bool:
    major = required_cuda_major(bin_dir)
    if major is None:
        return False
    names = [f"cublas64_{major}.dll", f"cublasLt64_{major}.dll"]
    search = [bin_dir, cuda_dir] + [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    return all(any((d / n).exists() for d in search) for n in names)


def engine_env(engine_cfg: dict) -> dict:
    """Entorno base para lanzar binarios del motor (PATH con bin y runtime CUDA)."""
    env = os.environ.copy()
    bin_dir = resolve(engine_cfg["bin_dir"])
    cuda_dir = resolve(engine_cfg["cuda_dir"])
    extra = [str(bin_dir)]
    if cuda_dir.exists():
        extra.append(str(cuda_dir))
    env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    env.pop("GGML_BACKEND", None)
    return env


def probe_devices(engine_cfg: dict) -> dict:
    """Lanza un binario del motor con GGML_BACKEND inválido para listar dispositivos."""
    bin_dir = resolve(engine_cfg["bin_dir"])
    models_dir = resolve(engine_cfg["models_dir"])
    result: dict = {
        "ok": False,
        "probed_at": time.time(),
        "devices": [],
        "cpu_variant": None,
        "error": None,
        "raw": "",
    }

    lm = sorted(models_dir.glob("acestep-5Hz-lm-*.gguf")) if models_dir.exists() else []
    vae = sorted(models_dir.glob("vae*.gguf")) if models_dir.exists() else []
    tmp = DATA / "probe"
    tmp.mkdir(parents=True, exist_ok=True)

    if lm and (bin_dir / f"ace-lm{EXE}").exists():
        req = tmp / "probe.json"
        req.write_text(
            json.dumps(
                {
                    "caption": "probe",
                    "lyrics": "[Instrumental]",
                    "duration": 10,
                    "bpm": 120,
                    "keyscale": "C major",
                    "timesignature": "4",
                    "vocal_language": "en",
                    "lm_model": lm[0].name,
                }
            ),
            encoding="utf-8",
        )
        cmd = [str(bin_dir / f"ace-lm{EXE}"), "--models", str(models_dir), "--request", str(req)]
    elif vae and (bin_dir / f"neural-codec{EXE}").exists():
        wav = tmp / "probe.wav"
        _tiny_wav(wav)
        cmd = [
            str(bin_dir / f"neural-codec{EXE}"),
            "--vae",
            str(vae[0]),
            "--encode",
            "-i",
            str(wav),
            "-o",
            str(tmp / "probe.vae"),
        ]
    else:
        result["error"] = "Se necesitan los binarios del motor y al menos el LM o el VAE para detectar dispositivos."
        return result

    env = engine_env(engine_cfg)
    env["GGML_BACKEND"] = "__openSuno_probe__"
    with _probe_lock:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                env=env,
                cwd=str(tmp),
                creationflags=_NO_WINDOW,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
        except (OSError, subprocess.SubprocessError) as e:
            result["error"] = f"No se pudo ejecutar el motor: {e}"
            return result

    result["raw"] = out[-4000:]
    names: dict[str, dict] = {}
    available: list[str] = []
    for line in out.splitlines():
        if m := _RE_AVAILABLE.search(line):
            available = m.group(1).split()
        elif m := _RE_VK.search(line):
            names[f"Vulkan{m.group(1)}"] = {"name": m.group(2).strip(), "uma": m.group(4) == "1"}
        elif m := _RE_CUDA.search(line):
            names[f"CUDA{m.group(1)}"] = {"name": m.group(2).strip(), "uma": False, "cc": m.group(3)}
        elif m := _RE_CPU_VARIANT.search(line):
            result["cpu_variant"] = m.group(1)

    if not available:
        result["error"] = "El motor no devolvió la lista de dispositivos."
        return result

    cpu = cpu_name()
    nv = {g["name"]: g for g in nvidia_gpus()}
    for dev in available:
        info = names.get(dev, {})
        if dev.startswith("CUDA"):
            kind = "cuda"
        elif dev.startswith("Vulkan"):
            kind = "vulkan"
        elif dev.startswith("CPU"):
            kind = "cpu"
        else:
            kind = "other"
        entry = {
            "id": dev,
            "kind": kind,
            "name": cpu if kind == "cpu" else info.get("name", dev),
            "integrated": bool(info.get("uma")),
        }
        mem = next((g["memory_total_mb"] for n, g in nv.items() if n in entry["name"]), None)
        if mem:
            entry["vram_mb"] = mem
        result["devices"].append(entry)

    result["ok"] = True
    return result


def best_gpu(devices: list[dict]) -> str | None:
    """CUDA primero; si no, la primera GPU Vulkan dedicada; si no, cualquier GPU."""
    cuda = [d for d in devices if d["kind"] == "cuda"]
    if cuda:
        return cuda[0]["id"]
    vk = [d for d in devices if d["kind"] == "vulkan"]
    discrete = [d for d in vk if not d.get("integrated")]
    if discrete:
        return discrete[0]["id"]
    return vk[0]["id"] if vk else None


# Medido con acestep.cpp (VAE con activaciones F32): ~9 MB de memoria por frame
# latente de cada tesela. Usamos 10 MB para dejar margen.
VAE_MB_PER_FRAME = 10


def recommend_vae_tiles(
    memory_mb: float | None, resident_mb: float, cap: int = 2048, gpu: bool = True
) -> tuple[int, int] | None:
    """Calcula vae_chunk/vae_overlap para que una tesela del VAE quepa en la memoria libre."""
    if not memory_mb:
        return None
    usable = memory_mb * 0.92 - 400 if gpu else memory_mb * 0.45
    budget = usable - resident_mb
    frames = int(budget / VAE_MB_PER_FRAME)
    chunk = max(128, min(cap, (frames // 64) * 64))
    return chunk, min(64, chunk // 4)


def load_cached_devices() -> dict | None:
    try:
        return json.loads(DEVICES_CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_cached_devices(data: dict) -> None:
    DEVICES_CACHE.parent.mkdir(parents=True, exist_ok=True)
    DEVICES_CACHE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
