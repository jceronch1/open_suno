"""Configuración persistente (data/settings.json) y perfiles CPU/GPU."""

from __future__ import annotations

import copy
import json
import re
import threading
from typing import Any

from .paths import DATA

SETTINGS_FILE = DATA / "settings.json"

# Opciones que se traducen a argumentos / entorno de ace-server.
ENGINE_DEFAULTS: dict[str, Any] = {
    # "auto" deja que GGML elija; si no, nombre exacto del dispositivo GGML:
    # "CPU", "CUDA0", "Vulkan0", "Vulkan1"...
    "device": "auto",
    "bin_dir": "engine/bin",
    "cuda_dir": "engine/cuda",
    "models_dir": "models",
    "adapters_dir": "adapters",
    "host": "127.0.0.1",
    "port": 8085,
    "keep_loaded": False,
    "vae_chunk": 1024,
    "vae_overlap": 64,
    "max_batch": 1,
    "max_seq": 8192,
    "flash_attention": True,
    "fsm": True,
    "batch_cfg": True,
    "clamp_fp16": False,
    "autostart": True,
    "profile": "custom",
}

DEFAULTS: dict[str, Any] = {
    "engine": ENGINE_DEFAULTS,
    "app": {
        "host": "127.0.0.1",
        "port": 7870,
        "open_browser": True,
    },
}

# Campos de "engine" que exigen reiniciar ace-server para aplicarse.
RESTART_KEYS = [k for k in ENGINE_DEFAULTS if k not in ("autostart", "profile")]

# Perfiles rápidos. "device" = "gpu" se resuelve con el mejor GPU detectado.
PROFILES: dict[str, dict[str, Any]] = {
    "cpu": {
        "label": "CPU",
        "description": "Todo en el procesador. Sin GPU. Mantiene los modelos en RAM para no recargarlos.",
        "values": {
            "device": "CPU",
            "keep_loaded": True,
            "vae_chunk": 1024,
            "vae_overlap": 64,
            "flash_attention": True,
            "batch_cfg": True,
            "clamp_fp16": False,
        },
    },
    "gpu": {
        "label": "GPU equilibrado",
        "description": "Mejor GPU detectada, modelos residentes en VRAM. Ideal para 8 GB con los modelos ligeros.",
        "values": {
            "device": "gpu",
            "keep_loaded": True,
            "vae_chunk": 512,
            "vae_overlap": 64,
            "flash_attention": True,
            "batch_cfg": True,
            "clamp_fp16": False,
        },
    },
    "gpu_low": {
        "label": "GPU ahorro de VRAM",
        "description": "Un solo módulo en VRAM a la vez y teselas VAE pequeñas. Para 4-6 GB o modelos grandes.",
        "values": {
            "device": "gpu",
            "keep_loaded": False,
            "vae_chunk": 256,
            "vae_overlap": 64,
            "flash_attention": True,
            "batch_cfg": True,
            "clamp_fp16": False,
        },
    },
    "gpu_high": {
        "label": "GPU máximo rendimiento",
        "description": "Todo residente y teselas VAE grandes. Para 12 GB de VRAM o más.",
        "values": {
            "device": "gpu",
            "keep_loaded": True,
            "vae_chunk": 1024,
            "vae_overlap": 64,
            "flash_attention": True,
            "batch_cfg": True,
            "clamp_fp16": False,
        },
    },
}


def _deep_merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# Límites razonables para que un valor absurdo no impida arrancar el motor.
RANGES: dict[str, tuple[int, int]] = {
    "port": (1024, 65535),
    "vae_chunk": (64, 8192),
    "vae_overlap": (0, 1024),
    "max_batch": (1, 16),
    "max_seq": (1024, 65536),
}
_HOST_RE = re.compile(r"^[\w.:-]{1,253}$")


def _coerce(section: dict, defaults: dict) -> dict:
    """Fuerza cada valor al tipo del valor por defecto (los formularios envían strings) y a su rango."""
    out = {}
    for k, default in defaults.items():
        v = section.get(k, default)
        try:
            if isinstance(default, bool):
                v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
            elif isinstance(default, int):
                v = int(v)
            elif isinstance(default, float):
                v = float(v)
            elif isinstance(default, str):
                v = str(v).strip()
        except (TypeError, ValueError):
            v = default
        if k in RANGES:
            lo, hi = RANGES[k]
            v = min(max(v, lo), hi)
        if k == "host" and not _HOST_RE.match(v):
            v = default
        if k.endswith("_dir") and not v:
            v = default
        out[k] = v
    if "vae_overlap" in out and "vae_chunk" in out:
        out["vae_overlap"] = min(out["vae_overlap"], out["vae_chunk"] // 2)
    return out


class SettingsStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> dict:
        data: dict = {}
        if SETTINGS_FILE.exists():
            try:
                data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
        merged = _deep_merge(DEFAULTS, data)
        merged["engine"] = _coerce(merged["engine"], ENGINE_DEFAULTS)
        merged["app"] = _coerce(merged["app"], DEFAULTS["app"])
        return merged

    def save(self) -> None:
        with self._lock:
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = SETTINGS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(SETTINGS_FILE)

    def get(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    @property
    def engine(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data["engine"])

    def update(self, patch: dict) -> dict:
        patch = {k: v for k, v in (patch or {}).items() if k in DEFAULTS and isinstance(v, dict)}
        with self._lock:
            merged = _deep_merge(self._data, patch)
            merged["engine"] = _coerce(merged["engine"], ENGINE_DEFAULTS)
            merged["app"] = _coerce(merged["app"], DEFAULTS["app"])
            self._data = merged
            self.save()
            return copy.deepcopy(self._data)
