"""Descarga del motor (binarios acestep.cpp), runtime CUDA y modelos GGUF."""

from __future__ import annotations

import collections
import hashlib
import json
import re
import secrets
import threading
import time
import zipfile
from pathlib import Path

import httpx

from .hardware import required_cuda_major
from .paths import DATA, IS_WINDOWS, resolve

HF_REPO = "Serveurperso/ACE-Step-1.5-GGUF"
HF_TREE = f"https://huggingface.co/api/models/{HF_REPO}/tree/main"
HF_FILE = f"https://huggingface.co/{HF_REPO}/resolve/main/{{name}}"
ENGINE_WIN_URL = "https://www.serveurperso.com/temp/acestep.cpp-win64/build/Release/"
PYPI_JSON = "https://pypi.org/pypi/{pkg}/json"
HF_CACHE = DATA / "hf_models.json"

# Los cuatro modelos de Open Suno (paquete ligero, apto para CPU y GPU de 4-8 GB).
RECOMMENDED = [
    "acestep-v15-turbo-Q4_K_M.gguf",
    "acestep-5Hz-lm-0.6B-Q8_0.gguf",
    "Qwen3-Embedding-0.6B-Q8_0.gguf",
    "vae-BF16.gguf",
]
FALLBACK_SIZES = {
    "acestep-v15-turbo-Q4_K_M.gguf": 1445710272,
    "acestep-5Hz-lm-0.6B-Q8_0.gguf": 709846656,
    "Qwen3-Embedding-0.6B-Q8_0.gguf": 784144960,
    "vae-BF16.gguf": 337420928,
}
ENGINE_FALLBACK_FILES = [
    "ace-server.exe", "ace-lm.exe", "ace-synth.exe", "ace-understand.exe", "neural-codec.exe",
    "mp3-codec.exe", "quantize.exe", "ggml.dll", "ggml-base.dll", "ggml-cpu-alderlake.dll",
    "ggml-cpu-cannonlake.dll", "ggml-cpu-cascadelake.dll", "ggml-cpu-haswell.dll", "ggml-cpu-icelake.dll",
    "ggml-cpu-sandybridge.dll", "ggml-cpu-skylakex.dll", "ggml-cpu-sse42.dll", "ggml-cpu-x64.dll",
    "ggml-vulkan.dll", "ggml-cuda.dll",
]  # fmt: skip

ROLE_INFO = {
    "dit": ("DiT (síntesis)", "Convierte los códigos del LM en audio latente. Turbo = 8 pasos, SFT/Base = 50 pasos."),
    "lm": ("LM (compositor)", "Qwen3 que escribe letra, metadatos y códigos de audio. 0.6B rápido, 4B mejor calidad."),
    "embedding": ("Codificador de texto", "Qwen3-Embedding: codifica la descripción para el DiT."),
    "vae": ("VAE", "Decodifica los latentes a audio estéreo 48 kHz."),
}


def classify(name: str) -> str | None:
    n = name.lower()
    if n.startswith("acestep-5hz-lm"):
        return "lm"
    if n.startswith("qwen3-embedding"):
        return "embedding"
    if n.startswith("vae"):
        return "vae"
    if n.startswith("acestep-v15"):
        return "dit"
    return None


def describe(name: str) -> dict:
    stem = name[:-5] if name.endswith(".gguf") else name
    m = re.search(r"-(BF16|F16|F32|Q\d_K_[SML]|Q\d_K|Q\d_\d)$", stem)
    quant = m.group(1) if m else ""
    base = stem[: m.start()] if m else stem
    role = classify(name) or "other"
    notes = []
    if role == "dit":
        v = base.replace("acestep-v15-", "")
        xl = v.startswith("xl-")
        v = v.replace("xl-", "")
        variant = {
            "turbo": "Turbo · 8 pasos",
            "sft": "SFT · 50 pasos, más calidad",
            "base": "Base · 50 pasos, permite lego/extract/complete",
            "sftturbo50": "SFT-Turbo · 50 pasos",
            "turbo-shift1": "Turbo shift 1",
            "turbo-shift3": "Turbo shift 3",
            "turbo-continuous": "Turbo continuo",
        }.get(v, v)
        notes.append(("XL 4B · " if xl else "2B · ") + variant)
    elif role == "lm":
        size = base.replace("acestep-5Hz-lm-", "")
        notes.append({"0.6B": "Rápido", "1.7B": "Equilibrado", "4B": "Mejor calidad"}.get(size, size))
    return {"name": name, "role": role, "quant": quant, "family": base, "note": " ".join(notes)}


class DownloadManager:
    def __init__(self, settings, engine=None) -> None:
        self.settings = settings
        self.engine = engine
        self.on_installed = None  # callback(kind) tras instalar motor/CUDA/modelo
        self.tasks: collections.OrderedDict[str, dict] = collections.OrderedDict()
        self.queue: collections.deque[str] = collections.deque()
        self._cond = threading.Condition()
        self._cancel: set[str] = set()
        self._hf_cache: tuple[float, list[dict]] | None = None
        threading.Thread(target=self._worker, daemon=True, name="openSuno-downloads").start()

    # ------------------------------------------------------------ catálogo
    def hf_models(self, refresh: bool = False) -> list[dict]:
        if self._hf_cache and not refresh and time.time() - self._hf_cache[0] < 3600:
            return self._hf_cache[1]
        files: list[dict] = []
        try:
            r = httpx.get(HF_TREE, timeout=15, follow_redirects=True)
            r.raise_for_status()
            files = [
                {"name": f["path"], "size": f.get("size"), "sha256": (f.get("lfs") or {}).get("oid")}
                for f in r.json()
                if f.get("type") == "file" and f["path"].endswith(".gguf")
            ]
            HF_CACHE.write_text(json.dumps(files), encoding="utf-8")
        except (httpx.HTTPError, ValueError, OSError):
            try:
                files = json.loads(HF_CACHE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                files = [{"name": n, "size": s} for n, s in FALLBACK_SIZES.items()]
        self._hf_cache = (time.time(), files)
        return files

    def catalog(self, refresh: bool = False) -> dict:
        cfg = self.settings.engine
        models_dir = resolve(cfg["models_dir"])
        installed = {p.name: p.stat().st_size for p in models_dir.glob("*.gguf")} if models_dir.exists() else {}
        remote = {f["name"]: f.get("size") for f in self.hf_models(refresh)}
        names = set(remote) | set(installed)
        models = []
        for n in sorted(names):
            d = describe(n)
            d.update(
                {
                    "size": remote.get(n) or installed.get(n),
                    "installed": n in installed,
                    "partial": (models_dir / (n + ".part")).exists(),
                    "recommended": n in RECOMMENDED,
                    "remote": n in remote,
                }
            )
            models.append(d)
        bin_dir = resolve(cfg["bin_dir"])
        cuda_dir = resolve(cfg["cuda_dir"])
        major = required_cuda_major(bin_dir)
        return {
            "models": models,
            "roles": {k: {"label": v[0], "help": v[1]} for k, v in ROLE_INFO.items()},
            "recommended": RECOMMENDED,
            "models_dir": str(models_dir),
            "engine": {
                "platform_supported": IS_WINDOWS,
                "source": ENGINE_WIN_URL,
                "bin_dir": str(bin_dir),
                "installed": (bin_dir / ("ace-server.exe" if IS_WINDOWS else "ace-server")).exists(),
                "has_cuda_backend": (bin_dir / "ggml-cuda.dll").exists(),
                "has_vulkan_backend": (bin_dir / "ggml-vulkan.dll").exists(),
            },
            "cuda": {
                "required_major": major,
                "dir": str(cuda_dir),
                "installed": bool(major) and all(
                    (cuda_dir / f).exists() or (bin_dir / f).exists()
                    for f in (f"cublas64_{major}.dll", f"cublasLt64_{major}.dll")
                ),
                "packages": [f"nvidia-cublas {major}.x", f"nvidia-cuda-runtime {major}.x"] if major else [],
            },
        }

    # --------------------------------------------------------------- tareas
    def list(self) -> list[dict]:
        with self._cond:
            return [dict(t) for t in reversed(self.tasks.values())]

    def _add(self, task: dict) -> dict:
        task.update(
            {
                "id": secrets.token_hex(5),
                "status": "queued",
                "done": 0,
                "total": task.get("total") or 0,
                "speed": 0,
                "error": None,
                "created_at": time.time(),
                "current_file": None,
            }
        )
        with self._cond:
            for t in self.tasks.values():  # no duplicar la misma descarga pendiente
                if t["key"] == task["key"] and t["status"] in ("queued", "running"):
                    return t
            self.tasks[task["id"]] = task
            self.queue.append(task["id"])
            self._cond.notify_all()
        return task

    def add_model(self, name: str) -> dict:
        if not name.endswith(".gguf") or "/" in name or "\\" in name:
            raise ValueError("nombre de modelo inválido")
        info = next((f for f in self.hf_models() if f["name"] == name), {})
        return self._add(
            {
                "key": f"model:{name}",
                "kind": "model",
                "label": name,
                "name": name,
                "total": info.get("size") or 0,
                "sha256": info.get("sha256"),
            }
        )

    def add_engine(self) -> dict:
        if not IS_WINDOWS:
            raise ValueError("Los binarios precompilados son sólo para Windows. En Linux/macOS compila acestep.cpp.")
        return self._add({"key": "engine", "kind": "engine", "label": "Motor acestep.cpp (CPU + CUDA + Vulkan)"})

    def add_cuda(self) -> dict:
        if not IS_WINDOWS:
            raise ValueError("El runtime CUDA automático es sólo para Windows.")
        major = required_cuda_major(resolve(self.settings.engine["bin_dir"]))
        if not major:
            raise ValueError("Instala primero el motor: no se encuentra ggml-cuda.dll.")
        return self._add({"key": "cuda", "kind": "cuda", "label": f"Runtime CUDA {major} (cuBLAS)", "major": major})

    def cancel(self, task_id: str | None = None) -> None:
        with self._cond:
            ids = [task_id] if task_id else [t["id"] for t in self.tasks.values()]
            for tid in ids:
                t = self.tasks.get(tid)
                if not t:
                    continue
                if t["status"] == "queued":
                    t["status"] = "cancelled"
                    try:
                        self.queue.remove(tid)
                    except ValueError:
                        pass
                elif t["status"] == "running":
                    self._cancel.add(tid)

    def clear_finished(self) -> None:
        with self._cond:
            for tid in [t["id"] for t in self.tasks.values() if t["status"] not in ("queued", "running")]:
                self.tasks.pop(tid)

    def active(self) -> bool:
        with self._cond:
            return any(t["status"] in ("queued", "running") for t in self.tasks.values())

    # ------------------------------------------------------------- worker
    def _worker(self) -> None:
        while True:
            with self._cond:
                while not self.queue:
                    self._cond.wait()
                tid = self.queue.popleft()
                task = self.tasks.get(tid)
                if not task or task["status"] != "queued":
                    continue
                task["status"] = "running"
            try:
                if task["kind"] == "model":
                    self._do_model(task)
                elif task["kind"] == "engine":
                    self._do_engine(task)
                elif task["kind"] == "cuda":
                    self._do_cuda(task)
                task["status"] = "done"
                if self.on_installed:
                    try:
                        self.on_installed(task["kind"])
                    except Exception:  # noqa: BLE001
                        pass
            except _Cancelled:
                task["status"] = "cancelled"
            except Exception as e:  # noqa: BLE001
                task["status"] = "failed"
                task["error"] = str(e) or e.__class__.__name__
            finally:
                self._cancel.discard(tid)
                task["speed"] = 0
                task["finished_at"] = time.time()

    def _fetch(
        self, task: dict, url: str, dest: Path, count_base: int = 0, size: int | None = None, sha256: str | None = None
    ) -> int:
        """Descarga con reanudación (.part) y, si se conocen, verifica tamaño y SHA-256 antes de
        dar el archivo por bueno. Devuelve los bytes del fichero final."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        task["current_file"] = dest.name
        with httpx.stream("GET", url, headers=headers, timeout=httpx.Timeout(30, read=120), follow_redirects=True) as r:
            if r.status_code == 416:  # ya completo
                self._verify(task, part, size, sha256)
                part.replace(dest)
                return dest.stat().st_size
            r.raise_for_status()
            if r.status_code == 200 and have:
                have = 0  # el servidor no admite Range: empezar de cero
            length = int(r.headers.get("content-length") or 0)
            file_total = have + length if length else 0
            if task["kind"] == "model" and file_total:
                task["total"] = file_total
            mode = "ab" if have else "wb"
            t_last, b_last = time.time(), have
            with open(part, mode) as fh:
                done = have
                for chunk in r.iter_bytes(1 << 20):
                    if task["id"] in self._cancel:
                        raise _Cancelled()
                    fh.write(chunk)
                    done += len(chunk)
                    task["done"] = count_base + done
                    now = time.time()
                    if now - t_last >= 0.5:
                        task["speed"] = (done - b_last) / (now - t_last)
                        t_last, b_last = now, done
        self._verify(task, part, size, sha256)
        part.replace(dest)
        return dest.stat().st_size

    def _verify(self, task: dict, part: Path, size: int | None, sha256: str | None) -> None:
        actual = part.stat().st_size
        if size and actual != size:
            if actual > size:
                part.unlink(missing_ok=True)
            raise RuntimeError(f"Descarga incompleta ({actual} de {size} bytes). Pulsa descargar para reanudar.")
        if sha256:
            task["current_file"] = f"Verificando {part.name[:-5]}"
            h = hashlib.sha256()
            with open(part, "rb") as fh:
                while chunk := fh.read(8 << 20):
                    if task["id"] in self._cancel:
                        raise _Cancelled()
                    h.update(chunk)
            if h.hexdigest() != sha256.lower():
                part.unlink(missing_ok=True)
                raise RuntimeError("El archivo descargado está dañado (SHA-256 no coincide). Vuelve a descargarlo.")

    def _do_model(self, task: dict) -> None:
        dest = resolve(self.settings.engine["models_dir"]) / task["name"]
        self._fetch(task, HF_FILE.format(name=task["name"]), dest, size=task.get("total") or None, sha256=task.get("sha256"))

    def _engine_files(self) -> list[str]:
        try:
            html = httpx.get(ENGINE_WIN_URL, timeout=20, follow_redirects=True).text
            names = sorted(set(re.findall(r'href="([\w.-]+\.(?:exe|dll))"', html)))
            names = [n for n in names if not n.startswith("test-") and n != "vulkan-shaders-gen.exe"]
            if "ace-server.exe" in names:
                return names
        except httpx.HTTPError:
            pass
        return ENGINE_FALLBACK_FILES

    def _do_engine(self, task: dict) -> None:
        bin_dir = resolve(self.settings.engine["bin_dir"])
        staging = bin_dir.parent / (bin_dir.name + ".new")
        staging.mkdir(parents=True, exist_ok=True)
        files = self._engine_files()
        sizes = {}
        with httpx.Client(timeout=20, follow_redirects=True) as c:
            for n in files:
                try:
                    sizes[n] = int(c.head(ENGINE_WIN_URL + n).headers.get("content-length") or 0)
                except httpx.HTTPError:
                    sizes[n] = 0
        task["total"] = sum(sizes.values())
        base = 0
        for n in files:
            self._fetch(task, ENGINE_WIN_URL + n, staging / n, count_base=base)
            base += sizes.get(n) or (staging / n).stat().st_size
        # Sustituye los binarios sólo cuando todo se ha descargado.
        task["current_file"] = "Instalando binarios"
        with self._engine_paused():
            bin_dir.mkdir(parents=True, exist_ok=True)
            for f in staging.iterdir():
                try:
                    f.replace(bin_dir / f.name)
                except PermissionError as e:
                    raise RuntimeError(f"No se pudo reemplazar {f.name}: ciérralo y reintenta.") from e
        staging.rmdir()

    class _Paused:
        def __init__(self, engine):
            self.engine = engine
            self.was_running = False

        def __enter__(self):
            if self.engine and self.engine.is_running():
                self.was_running = True
                self.engine.stop()
            return self

        def __exit__(self, *exc):
            if self.was_running:
                threading.Thread(target=self.engine.start, daemon=True).start()
            return False

    def _engine_paused(self):
        """Detiene el motor mientras se sobrescriben DLL en uso y lo rearranca después."""
        return DownloadManager._Paused(self.engine)

    def _do_cuda(self, task: dict) -> None:
        major = task["major"]
        cuda_dir = resolve(self.settings.engine["cuda_dir"])
        wheels = []
        for pkg in ("nvidia-cuda-runtime", "nvidia-cublas"):
            wheels.append(self._pypi_wheel(pkg, major))
        task["total"] = sum(w["size"] for w in wheels)
        base = 0
        tmp = DATA / "tmp"
        for w in wheels:
            path = tmp / w["filename"]
            self._fetch(task, w["url"], path, count_base=base)
            base += w["size"]
            task["current_file"] = f"Extrayendo {w['filename']}"
            with self._engine_paused(), zipfile.ZipFile(path) as z:
                for member in z.namelist():
                    fname = member.rsplit("/", 1)[-1]
                    if fname.lower().endswith(".dll") and not fname.lower().startswith("nvblas"):
                        cuda_dir.mkdir(parents=True, exist_ok=True)
                        with z.open(member) as src, open(cuda_dir / fname, "wb") as dst:
                            while chunk := src.read(1 << 20):
                                dst.write(chunk)
            path.unlink(missing_ok=True)

    def _pypi_wheel(self, pkg: str, major: int) -> dict:
        r = httpx.get(PYPI_JSON.format(pkg=pkg), timeout=20, follow_redirects=True)
        r.raise_for_status()
        releases = r.json()["releases"]

        def vkey(v: str):
            return [int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", v)]

        for version in sorted(releases, key=vkey, reverse=True):
            if not version.startswith(f"{major}."):
                continue
            for f in releases[version]:
                if f["filename"].endswith("win_amd64.whl"):
                    return {"url": f["url"], "size": f["size"], "filename": f["filename"]}
        raise RuntimeError(f"No hay rueda de {pkg} {major}.x para Windows en PyPI.")


class _Cancelled(Exception):
    pass
