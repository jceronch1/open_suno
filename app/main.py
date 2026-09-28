"""API y servidor web de Open Suno."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import psutil
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .downloads import DownloadManager, classify
from .engine import EngineManager
from .hardware import (
    best_gpu,
    cuda_runtime_present,
    load_cached_devices,
    nvidia_gpus,
    probe_devices,
    required_cuda_major,
    save_cached_devices,
    system_info,
)
from .jobs import JobManager, save_upload
from .library import Library
from .profiles import apply_profile
from .paths import DATA, IS_WINDOWS, WEB, ensure_dirs, resolve
from .settings import PROFILES, SettingsStore

ensure_dirs()
settings = SettingsStore()
engine = EngineManager(settings)
library = Library()
jobs = JobManager(engine, library)
downloads = DownloadManager(settings, engine)
PRESETS_FILE = DATA / "presets.json"
MAX_UPLOAD = 120 * 2**20

_devices_lock = threading.Lock()


def get_devices(refresh: bool = False) -> dict:
    with _devices_lock:
        cached = None if refresh else load_cached_devices()
        if cached and cached.get("ok"):
            return cached
        data = probe_devices(settings.engine)
        if data.get("ok"):
            save_cached_devices(data)
        return data


def _on_installed(kind: str) -> None:
    if kind in ("engine", "cuda") or (kind == "model" and not (load_cached_devices() or {}).get("ok")):
        threading.Thread(target=get_devices, kwargs={"refresh": True}, daemon=True).start()


downloads.on_installed = _on_installed


def local_models() -> dict:
    cfg = settings.engine
    models_dir = resolve(cfg["models_dir"])
    out: dict[str, list[str]] = {"lm": [], "dit": [], "embedding": [], "vae": []}
    if models_dir.exists():
        for p in sorted(models_dir.glob("*.gguf")):
            role = classify(p.name)
            if role:
                out[role].append(p.name)
    adapters_dir = resolve(cfg["adapters_dir"])
    adapters = []
    if adapters_dir.exists():
        for p in sorted(adapters_dir.iterdir()):
            if p.is_file() and p.suffix == ".safetensors":
                adapters.append(p.name)
            elif p.is_dir() and (p / "adapter_model.safetensors").exists():
                adapters.append(p.name)
    return {"models": out, "adapters": adapters, "models_dir": str(models_dir), "adapters_dir": str(adapters_dir)}


_nvidia_present: bool | None = None


def setup_status() -> dict:
    global _nvidia_present
    m = local_models()["models"]
    cfg = settings.engine
    bin_dir = resolve(cfg["bin_dir"])
    engine_ok = engine.binary_path().exists()
    if _nvidia_present is None:  # nvidia-smi es lento: se consulta una vez
        _nvidia_present = bool(nvidia_gpus())
    nvidia = _nvidia_present
    return {
        "engine": engine_ok,
        "lm": bool(m["lm"]),
        "dit": bool(m["dit"]),
        "embedding": bool(m["embedding"]),
        "vae": bool(m["vae"]),
        "ready": engine_ok and all(m[k] for k in ("lm", "dit", "embedding", "vae")),
        "cuda_required_major": required_cuda_major(bin_dir),
        "cuda_runtime": cuda_runtime_present(bin_dir, resolve(cfg["cuda_dir"])),
        "nvidia_gpu": nvidia,
    }


def _autostart() -> None:
    if not load_cached_devices() and setup_status()["engine"]:
        try:
            get_devices()
        except Exception:  # noqa: BLE001
            pass
    if settings.engine["autostart"] and setup_status()["ready"]:
        engine.start()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=_autostart, daemon=True).start()
    yield
    engine.stop()


app = FastAPI(title="Open Suno", version=__version__, lifespan=lifespan)


# ------------------------------------------------------------- seguridad
# 1) CSRF: toda petición que modifica algo debe llevar la cabecera X-Open-Suno. Una web
#    ajena no puede añadir cabeceras propias sin permiso CORS (que no damos).
# 2) DNS rebinding: desde este equipo sólo se aceptan nombres de host locales.
# 3) Acceso por red (--host 0.0.0.0): desde otros equipos se puede crear y escuchar,
#    pero no tocar el motor, rutas, descargas ni borrar modelos (salvo --allow-remote-admin).
_ADMIN_PREFIXES = ("/api/settings", "/api/engine/", "/api/downloads", "/api/models/", "/api/open-folder")
_LOCAL_NAMES = {"localhost", "127.0.0.1", "::1"}


def _local_addresses() -> set[str]:
    addrs = set(_LOCAL_NAMES)
    name = socket.gethostname().lower()
    addrs.update({name, f"{name}.local"})
    try:
        for entries in psutil.net_if_addrs().values():
            for a in entries:
                if a.family in (socket.AF_INET, socket.AF_INET6):
                    addrs.add(a.address.split("%")[0].lower())
    except (psutil.Error, OSError):
        pass
    return addrs


_LOCAL_ADDRS = _local_addresses()


@app.middleware("http")
async def _guard(request: Request, call_next):
    path = request.url.path
    client = (request.client.host if request.client else "").lower()
    from_this_pc = client in _LOCAL_ADDRS or client.startswith("127.")
    host = (request.headers.get("host") or "").lower()
    hostname = host[1 : host.find("]")] if host.startswith("[") else host.rsplit(":", 1)[0]
    if from_this_pc and hostname not in _LOCAL_ADDRS:
        return JSONResponse({"detail": "Host no permitido"}, status_code=403)
    if path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("x-open-suno") != "1":
            return JSONResponse({"detail": "Falta la cabecera X-Open-Suno"}, status_code=403)
        remote_admin = os.environ.get("OPENSUNO_ALLOW_REMOTE_ADMIN") == "1"
        if not from_this_pc and not remote_admin and path.startswith(_ADMIN_PREFIXES):
            return JSONResponse(
                {"detail": "Esta acción sólo se puede hacer desde el equipo donde se ejecuta Open Suno."},
                status_code=403,
            )
    response = await call_next(request)
    if not path.startswith("/api/"):
        # La interfaz se revalida siempre (ETag → 304): tras actualizar Open Suno nadie se
        # queda con un app.js antiguo en caché.
        response.headers["Cache-Control"] = "no-cache"
    return response


# ---------------------------------------------------------------- estado
@app.get("/api/state")
def api_state():
    return {
        "version": __version__,
        "engine": engine.status(),
        "setup": setup_status(),
        "jobs_active": jobs.active_count(),
        "downloads_active": downloads.active(),
        "library": library.stats(),
    }


@app.get("/api/hardware")
def api_hardware():
    return system_info()


@app.get("/api/devices")
def api_devices(refresh: bool = False):
    data = get_devices(refresh)
    data = {k: v for k, v in data.items() if k != "raw"} | {"raw": data.get("raw", "")[-1500:]}
    data["best_gpu"] = best_gpu(data.get("devices", []))
    st = setup_status()
    data["cuda_hint"] = bool(
        st["nvidia_gpu"] and not any(d["kind"] == "cuda" for d in data.get("devices", []))
    )
    data["cuda_runtime"] = st["cuda_runtime"]
    return data


# ------------------------------------------------------------ ajustes
@app.get("/api/settings")
def api_settings():
    return {"settings": settings.get(), "profiles": PROFILES, "needs_restart": engine.needs_restart()}


@app.put("/api/settings")
async def api_settings_put(request: Request):
    patch = await request.json()
    if "engine" in patch:
        patch["engine"] = dict(patch["engine"])
        patch["engine"].setdefault("profile", "custom")
    data = settings.update(patch)
    return {"settings": data, "needs_restart": engine.needs_restart()}


@app.post("/api/settings/profile/{name}")
def api_profile(name: str):
    if name not in PROFILES:
        raise HTTPException(404, "Perfil desconocido")
    try:
        data = apply_profile(settings, name, get_devices().get("devices", []))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"settings": data, "needs_restart": engine.needs_restart()}


# ---------------------------------------------------------------- motor
@app.get("/api/engine")
def api_engine():
    return engine.status()


@app.post("/api/engine/{action}")
async def api_engine_action(action: str):
    if action not in ("start", "stop", "restart"):
        raise HTTPException(404)
    fn = {"start": engine.start, "stop": engine.stop, "restart": engine.restart}[action]
    return await asyncio.to_thread(fn)


@app.get("/api/engine/props")
def api_engine_props():
    return engine.props() or {}


@app.get("/api/engine/logs")
def api_engine_logs(since: int = 0, limit: int = 500):
    last, lines = engine.logs_since(since, limit)
    return {"seq": last, "lines": lines}


@app.get("/api/engine/logs/stream")
async def api_engine_logs_stream(request: Request, since: int = 0):
    async def gen():
        seq = since
        last_ping = time.time()
        while not await request.is_disconnected():
            seq_new, lines = engine.logs_since(seq, 500)
            if lines:
                seq = seq_new
                yield f"data: {json.dumps(lines, ensure_ascii=False)}\n\n"
            elif time.time() - last_ping > 15:
                last_ping = time.time()
                yield ": ping\n\n"
            await asyncio.sleep(0.3)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# --------------------------------------------------------------- modelos
@app.get("/api/models")
def api_models():
    data = local_models()
    props = engine.props()
    if props:
        # Los nombres que usa el registro del motor son la fuente de verdad.
        data["engine_models"] = props.get("models")
        data["engine_adapters"] = props.get("adapters")
        data["engine_default"] = props.get("default")
        data["presets"] = props.get("presets")
    return data


@app.delete("/api/models/{name}")
def api_model_delete(name: str):
    if "/" in name or "\\" in name or not name.endswith(".gguf"):
        raise HTTPException(400, "Nombre inválido")
    path = resolve(settings.engine["models_dir"]) / name
    if not path.exists():
        raise HTTPException(404, "No existe")
    if engine.is_running():
        engine.stop()
    path.unlink()
    return {"ok": True}


# ------------------------------------------------------------- descargas
@app.get("/api/downloads/catalog")
def api_catalog(refresh: bool = False):
    return downloads.catalog(refresh)


@app.get("/api/downloads")
def api_downloads():
    return {"tasks": downloads.list()}


@app.post("/api/downloads")
async def api_downloads_add(request: Request):
    body = await request.json()
    added = []
    try:
        for item in body.get("items", []):
            if item == "engine":
                added.append(downloads.add_engine())
            elif item == "cuda":
                added.append(downloads.add_cuda())
            elif item == "recommended":
                cat = downloads.catalog()
                for m in cat["models"]:
                    if m["recommended"] and not m["installed"]:
                        added.append(downloads.add_model(m["name"]))
            elif item.startswith("model:"):
                added.append(downloads.add_model(item[6:]))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"tasks": added}


@app.post("/api/downloads/cancel")
async def api_downloads_cancel(request: Request):
    body = await request.json() if await request.body() else {}
    downloads.cancel(body.get("id"))
    return {"ok": True}


@app.post("/api/downloads/clear")
def api_downloads_clear():
    downloads.clear_finished()
    return {"ok": True}


# ------------------------------------------------------------ generación
def _song_source(song_id: str | None, kind: str) -> dict:
    """Usa el latente guardado de una canción de la biblioteca como fuente (evita recodificar)."""
    if not song_id:
        return {}
    lat = library.latent_path(song_id)
    if lat:
        return {f"{kind}_latents": lat}
    audio = library.audio_path(song_id)
    if audio:
        return {f"{kind}_audio": audio}
    raise HTTPException(404, "La canción de origen no existe")


@app.post("/api/generate")
async def api_generate(
    params: str = Form(...),
    src_audio: UploadFile | None = File(None),
    ref_audio: UploadFile | None = File(None),
):
    try:
        p = json.loads(params)
    except ValueError as e:
        raise HTTPException(400, "params no es JSON válido") from e
    if not setup_status()["ready"]:
        raise HTTPException(409, "Faltan el motor o modelos. Ve a la sección Modelos.")

    files: dict[str, Path] = {}
    upload_id = secrets.token_hex(6)

    async def read_upload(f: UploadFile) -> bytes:
        data = await f.read()
        if len(data) > MAX_UPLOAD:  # el motor admite ~10 min de WAV por petición
            raise HTTPException(413, f"El audio supera {MAX_UPLOAD // 2**20} MB. Usa MP3 o recórtalo.")
        return data

    if src_audio is not None and src_audio.filename:
        files["src_audio"] = save_upload(upload_id, src_audio.filename, await read_upload(src_audio))
    else:
        files.update(_song_source(p.get("src_song_id"), "src"))
    if ref_audio is not None and ref_audio.filename:
        files["ref_audio"] = save_upload(upload_id, "ref_" + ref_audio.filename, await read_upload(ref_audio))
    else:
        files.update(_song_source(p.get("ref_song_id"), "ref"))
    if p.get("task_type", "text2music") == "text2music":
        files = {k: v for k, v in files.items() if k.startswith("ref")}

    job = jobs.submit("song", p, files)
    return job.to_dict()


@app.post("/api/lyrics")
async def api_lyrics(request: Request):
    p = await request.json()
    if not (p.get("caption") or "").strip():
        raise HTTPException(400, "Escribe primero una descripción del estilo.")
    if not setup_status()["ready"]:
        raise HTTPException(409, "Faltan el motor o modelos.")
    job = jobs.submit("lyrics", p)
    return job.to_dict()


@app.get("/api/jobs")
def api_jobs():
    return {"jobs": jobs.list()}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404)
    return job.to_dict()


@app.post("/api/jobs/{job_id}/cancel")
def api_job_cancel(job_id: str):
    return {"ok": jobs.cancel(job_id)}


@app.delete("/api/jobs/{job_id}")
def api_job_delete(job_id: str):
    return {"ok": jobs.remove(job_id)}


@app.post("/api/jobs/clear")
def api_jobs_clear():
    jobs.clear_finished()
    return {"ok": True}


# ------------------------------------------------------------ biblioteca
@app.get("/api/songs")
def api_songs(q: str = "", favorites: bool = False, limit: int = 0):
    return {"songs": library.list(q, favorites, limit), "stats": library.stats()}


@app.get("/api/songs/{song_id}")
def api_song(song_id: str):
    s = library.get(song_id)
    if not s:
        raise HTTPException(404)
    return s


@app.patch("/api/songs/{song_id}")
async def api_song_patch(song_id: str, request: Request):
    s = library.update(song_id, await request.json())
    if not s:
        raise HTTPException(404)
    return s


@app.delete("/api/songs/{song_id}")
def api_song_delete(song_id: str):
    return {"ok": library.delete(song_id)}


def _safe_filename(title: str) -> str:
    keep = "".join(c for c in title if c.isalnum() or c in " -_()").strip()
    return keep[:80] or "cancion"


@app.get("/api/songs/{song_id}/audio")
def api_song_audio(song_id: str, download: bool = False):
    s = library.get(song_id)
    path = library.audio_path(song_id)
    if not s or not path:
        raise HTTPException(404)
    media = "audio/wav" if s["format"] == "wav" else "audio/mpeg"
    if download:
        name = f"{_safe_filename(s['title'])}.{s['format']}"
        return FileResponse(path, media_type=media, filename=name)
    return FileResponse(path, media_type=media)


# --------------------------------------------------------------- presets
def _read_presets() -> dict:
    try:
        return json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@app.get("/api/presets")
def api_presets():
    return {"presets": _read_presets()}


@app.post("/api/presets")
async def api_presets_save(request: Request):
    body = await request.json()
    name = (body.get("name") or "").strip()[:60]
    if not name:
        raise HTTPException(400, "Falta el nombre")
    presets = _read_presets()
    presets[name] = {"params": body.get("params", {}), "saved_at": time.time()}
    PRESETS_FILE.write_text(json.dumps(presets, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"presets": presets}


@app.delete("/api/presets/{name}")
def api_presets_delete(name: str):
    presets = _read_presets()
    presets.pop(name, None)
    PRESETS_FILE.write_text(json.dumps(presets, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"presets": presets}


# ------------------------------------------------------------ utilidades
@app.post("/api/open-folder/{which}")
def api_open_folder(which: str):
    cfg = settings.engine
    target = {
        "models": resolve(cfg["models_dir"]),
        "adapters": resolve(cfg["adapters_dir"]),
        "library": DATA / "library",
        "engine": resolve(cfg["bin_dir"]),
    }.get(which)
    if not target:
        raise HTTPException(404)
    target.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        os.startfile(str(target))  # noqa: S606
    else:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, str(target)])  # noqa: S603
    return {"ok": True, "path": str(target)}


@app.exception_handler(ValueError)
async def _value_error(_req, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
