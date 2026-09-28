"""Aplicación de perfiles CPU/GPU, compartida por la API y el instalador."""

from __future__ import annotations

import psutil

from .downloads import classify
from .hardware import best_gpu, recommend_vae_tiles
from .paths import resolve
from .settings import PROFILES


def largest_models_mb(models_dir) -> dict[str, float]:
    """Tamaño (MB) del mayor modelo instalado de cada tipo: estimación de lo que ocupará en memoria."""
    out: dict[str, float] = {}
    for p in models_dir.glob("*.gguf") if models_dir.exists() else []:
        role = classify(p.name)
        if role:
            out[role] = max(out.get(role, 0), p.stat().st_size / 2**20)
    return out


def profile_values(settings, name: str, devices: list[dict]) -> dict:
    """Valores de motor para un perfil, con dispositivo y teselas VAE ajustados al hardware real."""
    prof = PROFILES.get(name)
    if not prof:
        raise KeyError(name)
    values = dict(prof["values"])
    if values.get("device") == "gpu":
        gpu = best_gpu(devices)
        if not gpu:
            raise ValueError("No se detectó ninguna GPU utilizable por el motor.")
        values["device"] = gpu

    sizes = largest_models_mb(resolve(settings.engine["models_dir"]))
    if values["device"] == "CPU":
        tiles = recommend_vae_tiles(psutil.virtual_memory().total / 2**20, sum(sizes.values()), 1024, gpu=False)
    else:
        dev = next((d for d in devices if d["id"] == values["device"]), {})
        resident = (sum(sizes.values()) + 800) if values["keep_loaded"] else (sizes.get("vae", 350) + 300)
        cap = {"gpu_low": 512, "gpu": 1024, "gpu_high": 2048}.get(name, 1024)
        tiles = recommend_vae_tiles(dev.get("vram_mb"), resident, cap)
    if tiles:
        values["vae_chunk"], values["vae_overlap"] = tiles
    dev = next((d for d in devices if d["id"] == values["device"]), None)
    values["device_name"] = dev["name"] if dev and dev["kind"] != "cpu" else ""
    values["profile"] = name
    return values


def apply_profile(settings, name: str, devices: list[dict]) -> dict:
    return settings.update({"engine": profile_values(settings, name, devices)})
