"""Cola de trabajos: orquesta /lm → /synth en ace-server y guarda el resultado en la biblioteca."""

from __future__ import annotations

import collections
import json
import random
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

import httpx

from .library import Library, make_title
from .paths import UPLOADS

# Campos de AceRequest que acepta acestep.cpp y su tipo.
ACE_FIELDS: dict[str, type] = {
    "caption": str,
    "lyrics": str,
    "bpm": int,
    "duration": float,
    "keyscale": str,
    "timesignature": str,
    "vocal_language": str,
    "seed": int,
    "lm_seed": int,
    "lm_batch_size": int,
    "synth_batch_size": int,
    "lm_temperature": float,
    "lm_cfg_scale": float,
    "lm_top_p": float,
    "lm_top_k": int,
    "lm_negative_prompt": str,
    "use_cot_caption": bool,
    "audio_codes": str,
    "inference_steps": int,
    "guidance_scale": float,
    "shift": float,
    "dcw_scaler": float,
    "dcw_high_scaler": float,
    "dcw_mode": str,
    "audio_cover_strength": float,
    "cover_noise_strength": float,
    "repainting_start": float,
    "repainting_end": float,
    "latent_shift": float,
    "latent_rescale": float,
    "custom_timesteps": str,
    "task_type": str,
    "track": str,
    "solver": str,
    "stork_substeps": int,
    "lm_mode": str,
    "output_format": str,
    "peak_clip": int,
    "mp3_bitrate": int,
    "synth_model": str,
    "lm_model": str,
    "adapter": str,
    "adapter_scale": float,
}

# Campos que sólo afectan al DiT/VAE: se reaplican tras el paso del LM.
SYNTH_FIELDS = [
    "synth_batch_size", "inference_steps", "guidance_scale", "shift", "dcw_scaler", "dcw_high_scaler",
    "dcw_mode", "audio_cover_strength", "cover_noise_strength", "repainting_start", "repainting_end",
    "latent_shift", "latent_rescale", "custom_timesteps", "solver", "stork_substeps", "output_format",
    "peak_clip", "mp3_bitrate", "synth_model", "adapter", "adapter_scale", "task_type", "track",
]  # fmt: skip

# Tareas que usan el LM (según la tabla de compatibilidad de acestep.cpp).
LM_TASKS = {"text2music", "lego", "complete"}
SOURCE_TASKS = {"cover", "cover-nofsq", "repaint", "lego", "extract", "complete"}

STAGE_LABELS = {
    "queued": "En cola",
    "engine": "Iniciando motor",
    "lm": "Componiendo (LM)",
    "lm1": "Escribiendo letra y metadatos",
    "lm2": "Generando códigos de audio",
    "load": "Cargando modelos",
    "text": "Codificando texto",
    "dit": "Difusión (DiT)",
    "vae": "Decodificando audio (VAE)",
    "save": "Guardando",
    "done": "Completado",
}


def clean_request(params: dict) -> dict:
    """Filtra y tipa los campos AceRequest. Los valores vacíos se omiten (= valor por defecto)."""
    out: dict = {}
    for key, typ in ACE_FIELDS.items():
        if key not in params:
            continue
        v = params[key]
        if v is None or (isinstance(v, str) and v.strip() == "" and key != "lyrics"):
            continue
        try:
            if typ is bool:
                v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "on", "yes")
            elif typ is int:
                v = int(float(v))
            elif typ is float:
                v = float(v)
            else:
                v = str(v)
        except (TypeError, ValueError):
            continue
        if key == "lyrics" and v == "":
            continue
        out[key] = v
    return out


class Job:
    def __init__(self, kind: str, params: dict, files: dict | None = None) -> None:
        self.id = secrets.token_hex(6)
        self.kind = kind  # "song" | "lyrics"
        self.params = params
        self.files = files or {}
        self.status = "queued"  # queued | running | done | failed | cancelled
        self.stage = "queued"
        self.progress = 0.0
        self.detail = ""
        self.error: str | None = None
        self.created_at = time.time()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.song_ids: list[str] = []
        self.result: dict | None = None
        self.engine_job: str | None = None
        self.cancel = False
        self.timings: dict[str, float] = {}
        self.use_lm = True

    def to_dict(self) -> dict:
        p = self.params
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "stage": self.stage,
            "stage_label": STAGE_LABELS.get(self.stage, self.stage),
            "progress": round(self.progress, 4),
            "detail": self.detail,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed": ((self.finished_at or time.time()) - self.started_at) if self.started_at else 0,
            "song_ids": self.song_ids,
            "result": self.result,
            "title": p.get("title") or make_title(p.get("lyrics", ""), p.get("caption", "")),
            "caption": p.get("caption", ""),
            "task_type": p.get("task_type", "text2music"),
            "timings": self.timings,
        }


class JobManager:
    def __init__(self, engine, library: Library) -> None:
        self.engine = engine
        self.library = library
        self.jobs: collections.OrderedDict[str, Job] = collections.OrderedDict()
        self.queue: collections.deque[Job] = collections.deque()
        self.current: Job | None = None
        self._cond = threading.Condition()
        self._phase2_max = 0
        self._synth_seq = 0
        engine.add_listener(self._on_log)
        threading.Thread(target=self._worker, daemon=True, name="openSuno-jobs").start()

    # ----------------------------------------------------------------- API
    def submit(self, kind: str, params: dict, files: dict | None = None) -> Job:
        job = Job(kind, params, files)
        with self._cond:
            self.jobs[job.id] = job
            while len(self.jobs) > 200:
                old_id, old = next(iter(self.jobs.items()))
                if old.status in ("queued", "running"):
                    break
                self.jobs.pop(old_id)
            self.queue.append(job)
            self._cond.notify_all()
        return job

    def list(self) -> list[dict]:
        with self._cond:
            return [j.to_dict() for j in reversed(self.jobs.values())]

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        with self._cond:
            job = self.jobs.get(job_id)
            if not job or job.status not in ("queued", "running"):
                return False
            job.cancel = True
            if job.status == "queued":
                try:
                    self.queue.remove(job)
                except ValueError:
                    pass
                self._finish(job, "cancelled")
            return True

    def remove(self, job_id: str) -> bool:
        with self._cond:
            job = self.jobs.get(job_id)
            if not job or job.status in ("queued", "running"):
                return False
            self.jobs.pop(job_id)
            return True

    def clear_finished(self) -> None:
        with self._cond:
            for jid in [j.id for j in self.jobs.values() if j.status not in ("queued", "running")]:
                self.jobs.pop(jid)

    def active_count(self) -> int:
        with self._cond:  # submit() puede insertar a la vez desde otro hilo
            return sum(1 for j in self.jobs.values() if j.status in ("queued", "running"))

    # ------------------------------------------------------------ progreso
    def _set(self, job: Job, stage: str | None = None, progress: float | None = None, detail: str | None = None):
        if stage:
            job.stage = stage
        if progress is not None:
            job.progress = max(job.progress, min(progress, 0.999))
        if detail is not None:
            job.detail = detail

    def _span(self, job: Job, part: str) -> tuple[float, float]:
        """Rango de la barra de progreso reservado a cada fase."""
        if job.kind == "lyrics":
            return {"lm1": (0.05, 0.95), "lm2": (0.05, 0.95)}.get(part, (0.0, 1.0))
        if job.use_lm:
            spans = {"lm1": (0.03, 0.18), "lm2": (0.18, 0.5), "text": (0.5, 0.55), "dit": (0.55, 0.86), "vae": (0.86, 0.98)}
        else:
            spans = {"lm1": (0, 0), "lm2": (0, 0), "text": (0.05, 0.12), "dit": (0.12, 0.8), "vae": (0.8, 0.98)}
        return spans.get(part, (0.0, 1.0))

    def _on_log(self, line: str) -> None:
        job = self.current
        if not job or job.status != "running":
            return
        if line.startswith("[Load]") or "[Store]" in line and "load" in line.lower():
            if job.stage not in ("dit", "vae"):
                self._set(job, detail="Cargando modelos en " + (self.engine.backend or "el dispositivo"))
        elif line.startswith("[LM-Phase1] Step"):
            m = re.search(r"Step (\d+)", line)
            n = int(m.group(1)) if m else 0
            a, b = self._span(job, "lm1")
            self._set(job, "lm1", a + (b - a) * min(n / 700, 1.0), f"{n} tokens de letra/metadatos")
        elif line.startswith("[LM-Phase1]"):
            self._set(job, "lm1", self._span(job, "lm1")[0])
        elif line.startswith("[LM-Phase2] max_tokens"):
            m = re.search(r"max_tokens:\s*(\d+)", line)
            self._phase2_max = int(m.group(1)) if m else 0
            self._set(job, "lm2", self._span(job, "lm2")[0], "iniciando…")
        elif line.startswith("[LM-Phase2] Step"):
            m = re.search(r"Step (\d+).*?(\d+) total codes(?:,\s*([\d.]+) tok/s)?", line)
            if m:
                step = int(m.group(1))
                a, b = self._span(job, "lm2")
                frac = step / self._phase2_max if self._phase2_max else 0.5
                speed = f" · {m.group(3)} tok/s" if m.group(3) else ""
                self._set(job, "lm2", a + (b - a) * min(frac, 1.0), f"{m.group(2)} códigos de audio{speed}")
        elif line.startswith("[Encode-Text") or line.startswith("[Resolve-T]"):
            self._set(job, "text", self._span(job, "text")[0])
        elif line.startswith("[DiT] Step"):
            m = re.search(r"Step (\d+)/(\d+)", line)
            if m:
                a, b = self._span(job, "dit")
                s, t = int(m.group(1)), int(m.group(2))
                self._set(job, "dit", a + (b - a) * s / max(t, 1), f"Paso {s} de {t}")
        elif line.startswith("[VAE] Tiled decode:") or line.startswith("[VAE-Decode"):
            a, b = self._span(job, "vae")
            m = re.search(r"(\d+) tiles", line)
            self._set(job, "vae", a + (b - a) * (0.5 if "Decode:" in line else 0.1),
                      f"{m.group(1)} teselas" if m else None)  # fmt: skip

    # ----------------------------------------------------------- ejecución
    def _worker(self) -> None:
        while True:
            with self._cond:
                while not self.queue:
                    self._cond.wait()
                job = self.queue.popleft()
                if job.cancel:
                    continue
                job.status = "running"
                job.started_at = time.time()
                job.stage, job.detail = "load", "Preparando…"
                self.current = job
            try:
                if not self.engine.is_running():
                    self._set(job, "engine", 0.01, "Arrancando ace-server…")
                    if not self.engine.ensure_running():
                        raise RuntimeError(self.engine.error or "No se pudo iniciar el motor.")
                if job.kind == "lyrics":
                    self._run_lyrics(job)
                else:
                    self._run_song(job)
                self._finish(job, "done")
            except _Cancelled:
                self._finish(job, "cancelled")
            except Exception as e:  # noqa: BLE001
                job.error = str(e) or e.__class__.__name__
                self._finish(job, "failed")
            finally:
                self.current = None
                self._cleanup_files(job)

    def _finish(self, job: Job, status: str) -> None:
        job.status = status
        job.finished_at = time.time()
        if status == "done":
            job.stage, job.progress = "done", 1.0

    def _cleanup_files(self, job: Job) -> None:
        """Borra sólo los audios subidos para este trabajo (nunca los de la biblioteca)."""
        uploads = UPLOADS.resolve()
        for path in job.files.values():
            folder = Path(path).resolve().parent
            if folder.parent == uploads and folder.exists():
                shutil.rmtree(folder, ignore_errors=True)

    # --------------------------------------------------------- motor HTTP
    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.engine.base_url(), timeout=httpx.Timeout(60, read=600))

    def _engine_error(self, since_seq: int) -> str:
        _, lines = self.engine.logs_since(since_seq, 5000)
        bad = [l["line"] for l in lines if re.search(r"FATAL|ERROR|error|not found|failed|OutOfDeviceMemory", l["line"])]
        if not bad:
            return "el motor informó de un fallo (revisa el registro del motor)"
        oom = [b for b in bad if re.search(r"OutOfDeviceMemory|out of memory|failed to allocate", b, re.I)]
        return " · ".join(dict.fromkeys((oom[:1] + bad[-2:])))

    def _submit_and_wait(self, client: httpx.Client, job: Job, path: str, **kwargs) -> httpx.Response:
        seq0, _ = self.engine.logs_since(10**12)
        r = client.post(path, **kwargs)
        if r.status_code != 200:
            try:
                msg = r.json().get("error", r.text)
            except ValueError:
                msg = r.text
            raise RuntimeError(f"{path}: {msg} (HTTP {r.status_code})")
        eid = r.json()["id"]
        job.engine_job = eid
        cancelled_sent = False
        while True:
            if job.cancel and not cancelled_sent:
                try:
                    client.post("/job", params={"id": eid, "cancel": 1})
                except httpx.HTTPError:
                    pass
                cancelled_sent = True
            if not self.engine.is_running():
                raise RuntimeError(self.engine.error or "El motor se detuvo durante la generación.")
            try:
                st = client.get("/job", params={"id": eid}, timeout=10).json().get("status")
            except (httpx.HTTPError, ValueError):
                st = "running"
            if st == "done":
                break
            if st == "cancelled":
                raise _Cancelled()
            if st == "failed":
                if job.cancel:
                    raise _Cancelled()
                raise RuntimeError(self._engine_error(seq0))
            if st not in ("running", "queued", None):
                raise RuntimeError(f"Estado desconocido del motor: {st}")
            time.sleep(0.35)
        res = client.get("/job", params={"id": eid, "result": 1})
        if res.status_code != 200:
            raise RuntimeError(f"No se pudo leer el resultado ({res.status_code})")
        return res

    # --------------------------------------------------------------- letra
    def _run_lyrics(self, job: Job) -> None:
        req = clean_request(job.params)
        req["lm_mode"] = job.params.get("lm_mode") or "inspire"
        req.pop("audio_codes", None)
        req["lm_batch_size"] = 1
        self._set(job, "lm1", 0.03, "Pidiendo al LM…")
        t0 = time.time()
        item: dict = {}
        with self._client() as client:
            for _attempt in range(3):
                seq0, _ = self.engine.logs_since(10**12)
                res = self._submit_and_wait(client, job, "/lm", json=req)
                out = res.json()
                item = (out[0] if out else {}) if isinstance(out, list) else (out or {})
                if not item:
                    # acestep.cpp devuelve [] si el LM emitió UTF-8 inválido (no puede
                    # serializarlo); la salida completa sí queda en su registro.
                    item = self._lm_output_from_log(seq0)
                if item.get("lyrics"):
                    break
                self.engine._log("[Open Suno] El LM devolvió un resultado vacío; reintentando con otra semilla…")
        job.timings["lm"] = round(time.time() - t0, 2)
        if not item.get("lyrics"):
            raise RuntimeError("El LM no devolvió letra. Prueba otra descripción o sube la temperatura.")
        keep = ("caption", "lyrics", "bpm", "duration", "keyscale", "timesignature", "vocal_language")
        job.result = {k: item.get(k) for k in keep if k in item}

    def _lm_output_from_log(self, since_seq: int) -> dict:
        """Reconstruye metadatos y letra a partir del volcado de texto del LM en el registro."""
        _, lines = self.engine.logs_since(since_seq, 5000)
        text = [l["line"] for l in lines]
        start = max((i for i, l in enumerate(text) if re.match(r"^\[(inspire|format|generate) Batch\d+\]", l)), default=-1)
        if start < 0:
            return {}
        meta: dict = {}
        lyrics: list[str] = []
        in_lyrics = False
        for line in text[start + 1 :]:
            if line.startswith("[LM-Generate]") or line.startswith("[Ace-LM]") or line.startswith("[Server]"):
                break
            if in_lyrics:
                lyrics.append(line)
            elif line.strip() == "# Lyric":
                in_lyrics = True
            elif m := re.match(r"^(bpm|duration|keyscale|language|timesignature):\s*(.+)$", line):
                meta[m.group(1)] = m.group(2).strip()
        out: dict = {}
        if lyrics:
            out["lyrics"] = "\n".join(lyrics).strip()
        try:
            if "bpm" in meta:
                out["bpm"] = int(meta["bpm"])
            if "duration" in meta:
                out["duration"] = float(meta["duration"])
        except ValueError:
            pass
        if "keyscale" in meta:
            out["keyscale"] = meta["keyscale"]
        if "timesignature" in meta:
            out["timesignature"] = meta["timesignature"]
        if "language" in meta:
            out["vocal_language"] = meta["language"]
        return out

    # ------------------------------------------------------------- canción
    def _run_song(self, job: Job) -> None:
        req = clean_request(job.params)
        task = req.get("task_type", "text2music")
        req.setdefault("output_format", "mp3")
        if not req.get("caption") and task not in ("lego", "extract", "complete"):
            raise RuntimeError("La descripción (caption) es obligatoria.")

        wants_lm = bool(job.params.get("use_lm", True))
        job.use_lm = wants_lm and task in LM_TASKS and not req.get("audio_codes")
        if task in SOURCE_TASKS and not (job.files.get("src_audio") or job.files.get("src_latents")):
            raise RuntimeError("Esta tarea necesita un audio de origen.")

        max_batch = max(1, int(self.engine.settings.engine["max_batch"]))
        if req.get("lm_batch_size", 1) > max_batch:
            req["lm_batch_size"] = max_batch

        t_start = time.time()
        with self._client() as client:
            # 1) LM: letra, metadatos y códigos de audio
            if job.use_lm:
                self._set(job, "lm", 0.02, "Preparando el modelo de lenguaje…")
                t0 = time.time()
                lm_req = dict(req, lm_mode="generate")
                reqs: list = []
                for attempt in range(3):
                    res = self._submit_and_wait(client, job, "/lm", json=lm_req)
                    reqs = res.json()
                    if isinstance(reqs, dict):
                        reqs = [reqs]
                    if reqs:
                        break
                    # [] = el LM emitió UTF-8 inválido y el motor no pudo serializarlo.
                    self.engine._log("[Open Suno] El LM devolvió un resultado vacío; reintentando con otra semilla…")
                    lm_req["lm_seed"] = random.randint(0, 2**31 - 1)
                    self._set(job, "lm", detail=f"Reintento {attempt + 2} del LM")
                if not reqs:
                    raise RuntimeError("El LM no devolvió resultado tras 3 intentos. Prueba otra descripción.")
                job.timings["lm"] = round(time.time() - t0, 2)
                for r in reqs:  # conserva los ajustes de síntesis elegidos por el usuario
                    for k in SYNTH_FIELDS:
                        if k in req:
                            r[k] = req[k]
            else:
                r = dict(req)
                if task == "text2music" and not r.get("duration"):
                    r["duration"] = 60.0
                if int(r.get("seed", -1)) < 0:
                    r["seed"] = random.randint(0, 2**31 - 1)
                reqs = [r]

            if job.cancel:
                raise _Cancelled()

            # 2) DiT + VAE
            self._set(job, "text", self._span(job, "text")[0], "Preparando síntesis…")
            t0 = time.time()
            self._synth_seq, _ = self.engine.logs_since(10**12)
            if job.files:
                data = {"request": json.dumps(reqs, ensure_ascii=False)}
                files = {}
                handles = []
                for part in ("src_audio", "src_latents", "ref_audio", "ref_latents"):
                    path: Path | None = job.files.get(part)
                    if path:
                        fh = open(path, "rb")
                        handles.append(fh)
                        field = "audio" if part == "src_audio" else part
                        mime = "application/octet-stream" if part.endswith("latents") else "audio/mpeg"
                        files[field] = (path.name, fh, mime)
                try:
                    res = self._submit_and_wait(client, job, "/synth", data=data, files=files)
                finally:
                    for fh in handles:
                        fh.close()
            else:
                res = self._submit_and_wait(client, job, "/synth", json=reqs)
            job.timings["synth"] = round(time.time() - t0, 2)

        # 3) Guardar pistas
        self._set(job, "save", 0.99, "Guardando en la biblioteca…")
        tracks = parse_multipart(res.content, res.headers.get("content-type", ""))
        if not tracks:
            reason = self._engine_error(self._synth_seq)
            if re.search(r"OutOfDeviceMemory|out of memory|failed to allocate|graph alloc failed", reason, re.I):
                raise RuntimeError(
                    "Memoria insuficiente al decodificar el audio (VAE). Reduce «VAE chunk» en Motor, "
                    "desactiva «Mantener modelos cargados» o aplica el perfil «GPU ahorro de VRAM». "
                    f"Detalle: {reason}"
                )
            raise RuntimeError(f"El motor no devolvió audio: {reason}")

        expanded: list[dict] = []
        for r in reqs:
            n = max(1, min(int(r.get("synth_batch_size", 1) or 1), 9))
            for i in range(n):
                v = dict(r)
                if "seed" in v and int(v["seed"]) >= 0:
                    v["seed"] = int(v["seed"]) + i
                v["synth_batch_size"] = 1
                expanded.append(v)

        job.timings["total"] = round(time.time() - t_start, 2)
        cfg = self.engine.launch_cfg or {}
        for idx, (audio, mime, latent) in enumerate(tracks):
            final = expanded[idx] if idx < len(expanded) else expanded[-1]
            final.pop("audio_codes", None)  # muy largo; no aporta al usuario
            # Para instrumentales, la descripción del usuario da mejores títulos que la enriquecida por el LM.
            title = job.params.get("title") or make_title(
                final.get("lyrics", ""), job.params.get("caption") or final.get("caption", "")
            )
            meta = {
                "title": title,
                "variant": idx + 1,
                "variants": len(tracks),
                "job_id": job.id,
                "request": final,
                "user_params": {k: v for k, v in job.params.items() if k != "audio_codes"},
                "task_type": task,
                "used_lm": job.use_lm,
                "device": cfg.get("device"),
                "backend": self.engine.backend,
                "timings": dict(job.timings),
                "source_song_id": job.params.get("src_song_id"),
            }
            song = self.library.add(audio, mime, latent, meta)
            job.song_ids.append(song["id"])


class _Cancelled(Exception):
    pass


def parse_multipart(body: bytes, content_type: str) -> list[tuple[bytes, str, bytes | None]]:
    """Separa la respuesta multipart/mixed de /synth en (audio, mime, latente) por pista."""
    m = re.search(r'boundary="?([^";]+)"?', content_type)
    boundary = (m.group(1) if m else "ace-batch-boundary").encode()
    # Los delimitadores siempre van al inicio de línea (CRLF + "--boundary"); partir así evita
    # confundirlos con la misma secuencia de bytes dentro del audio o del latente binario.
    parts = re.split(b"\r\n--" + re.escape(boundary) + b"(?=--|\r\n)", b"\r\n" + body)
    tracks: list[tuple[bytes, str, bytes | None]] = []
    for part in parts[1:]:
        if part.startswith(b"--"):
            break
        if part.startswith(b"\r\n"):
            part = part[2:]
        head, sep, content = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        headers = head.decode("latin-1").lower()
        ct = re.search(r"content-type:\s*([^\r\n]+)", headers)
        ctype = ct.group(1).strip() if ct else ""
        if ctype.startswith("audio/"):
            tracks.append((content, ctype, None))
        elif 'name="latent"' in headers and tracks and tracks[-1][2] is None:
            a, t, _ = tracks[-1]
            tracks[-1] = (a, t, content)
    return tracks


def save_upload(upload_id: str, name: str, data: bytes) -> Path:
    folder = UPLOADS / upload_id
    folder.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.-]", "_", name)[-80:] or "audio"
    path = folder / safe
    path.write_bytes(data)
    return path
