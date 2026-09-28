"""Gestor del proceso ace-server (acestep.cpp): arranque CPU/GPU, logs y estado."""

from __future__ import annotations

import collections
import re
import subprocess
import threading
import time
from typing import Callable

import httpx
import psutil

from .hardware import engine_env
from .paths import DATA, EXE, IS_WINDOWS, resolve
from .settings import RESTART_KEYS

_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
_RE_BACKEND = re.compile(r"\[Load\]\s+(\S+)\s+backend:\s+(\S+)(?:\s+\(CPU threads:\s*(\d+)\))?")
_RE_VERSION = re.compile(r"acestep\.cpp\s+(\S+\s*\([^)]*\))")

LogListener = Callable[[str], None]


class _KillOnExitJob:
    """Job Object de Windows: si Open Suno muere (p. ej. se cierra la consola), ace-server muere con él."""

    def __init__(self) -> None:
        self.handle = None
        if not IS_WINDOWS:
            return
        try:
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.OpenProcess.restype = wintypes.HANDLE

            class BASIC(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class IO(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

            class EXTENDED(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", BASIC),
                    ("IoInfo", IO),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            job = k32.CreateJobObjectW(None, None)
            info = EXTENDED()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            ok = k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
            if job and ok:
                self.handle, self._k32 = job, k32
        except (OSError, AttributeError):
            self.handle = None

    def assign(self, pid: int) -> None:
        if not self.handle:
            return
        h = self._k32.OpenProcess(0x0001 | 0x0100, False, pid)  # PROCESS_TERMINATE | PROCESS_SET_QUOTA
        if h:
            self._k32.AssignProcessToJobObject(self.handle, h)
            self._k32.CloseHandle(h)


_JOB = _KillOnExitJob()


def _die_with_parent() -> None:
    """Linux: pide al kernel que mande SIGTERM a ace-server si Open Suno muere."""
    try:
        import ctypes
        import signal

        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass  # macOS u otros: sin equivalente directo


class EngineManager:
    def __init__(self, settings) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self.proc: subprocess.Popen | None = None
        self.state = "stopped"  # stopped | starting | running | stopping | error
        self.error: str | None = None
        self.started_at: float | None = None
        self.launch_cfg: dict | None = None
        self.backend: str | None = None
        self.cpu_threads: int | None = None
        self.version: str | None = None
        self.logs: collections.deque[tuple[int, float, str]] = collections.deque(maxlen=4000)
        self._seq = 0
        self._log_cond = threading.Condition()
        self._listeners: list[LogListener] = []
        self._log_file = DATA / "engine.log"

    # ------------------------------------------------------------------ util
    def base_url(self, cfg: dict | None = None) -> str:
        cfg = cfg or self.launch_cfg or self.settings.engine
        host = cfg["host"]
        if host in ("0.0.0.0", "::", ""):
            host = "127.0.0.1"
        return f"http://{host}:{cfg['port']}"

    def add_listener(self, fn: LogListener) -> None:
        self._listeners.append(fn)

    def _log(self, line: str) -> None:
        line = line.rstrip("\r\n")
        if not line:
            return
        with self._log_cond:
            self._seq += 1
            self.logs.append((self._seq, time.time(), line))
            self._log_cond.notify_all()
        if m := _RE_BACKEND.search(line):
            self.backend = m.group(2)
            if m.group(3):
                self.cpu_threads = int(m.group(3))
        for fn in list(self._listeners):
            try:
                fn(line)
            except Exception:  # noqa: BLE001 - un listener roto no debe tumbar el lector
                pass

    def logs_since(self, seq: int, limit: int = 1000) -> tuple[int, list[dict]]:
        with self._log_cond:
            items = [e for e in self.logs if e[0] > seq][-limit:]
            last = self._seq
        return last, [{"seq": s, "t": t, "line": l} for s, t, l in items]

    def wait_logs(self, seq: int, timeout: float = 15.0) -> None:
        with self._log_cond:
            if self._seq <= seq:
                self._log_cond.wait(timeout)

    def binary_path(self, cfg: dict | None = None):
        cfg = cfg or self.settings.engine
        return resolve(cfg["bin_dir"]) / f"ace-server{EXE}"

    _version_cache: tuple[str, float, str | None] | None = None

    def binary_version(self) -> str | None:
        """Versión de ace-server (la imprime en la ayuda). Cacheada por fecha del binario."""
        exe = self.binary_path()
        if not exe.exists():
            return None
        mtime = exe.stat().st_mtime
        cache = EngineManager._version_cache
        if cache and cache[0] == str(exe) and cache[1] == mtime:
            return cache[2]
        version = None
        try:
            out = subprocess.run(
                [str(exe)], capture_output=True, timeout=15, cwd=str(exe.parent), creationflags=_NO_WINDOW
            )
            text = (out.stdout + out.stderr).decode("utf-8", errors="replace")
            if m := _RE_VERSION.search(text):
                version = m.group(1)
        except (OSError, subprocess.SubprocessError):
            pass
        EngineManager._version_cache = (str(exe), mtime, version)
        return version

    # ------------------------------------------------------------ comandos
    def build_command(self, cfg: dict, device: str) -> tuple[list[str], dict]:
        exe = self.binary_path(cfg)
        models = resolve(cfg["models_dir"])
        adapters = resolve(cfg["adapters_dir"])
        adapters.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(exe),
            "--models", str(models),
            "--adapters", str(adapters),
            "--host", cfg["host"],
            "--port", str(cfg["port"]),
            "--max-batch", str(max(1, cfg["max_batch"])),
            "--max-seq", str(cfg["max_seq"]),
            "--vae-chunk", str(cfg["vae_chunk"]),
            "--vae-overlap", str(cfg["vae_overlap"]),
        ]  # fmt: skip
        if cfg["keep_loaded"]:
            cmd.append("--keep-loaded")
        if not cfg["flash_attention"]:
            cmd.append("--no-fa")
        if not cfg["fsm"]:
            cmd.append("--no-fsm")
        if not cfg["batch_cfg"]:
            cmd.append("--no-batch-cfg")
        if cfg["clamp_fp16"]:
            cmd.append("--clamp-fp16")

        env = engine_env(cfg)
        if device and device != "auto":
            env["GGML_BACKEND"] = device
        return cmd, env

    def _kill_orphan_on_port(self, port: int) -> None:
        """Si un ace-server huérfano (de una ejecución anterior) ocupa el puerto, lo cierra."""
        try:
            conns = psutil.net_connections(kind="tcp")
        except (psutil.Error, OSError):
            return
        for c in conns:
            if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN and c.pid:
                try:
                    p = psutil.Process(c.pid)
                    if p.name().lower().startswith("ace-server"):
                        self._log(f"[Open Suno] Cerrando ace-server previo (PID {c.pid}) en el puerto {port}")
                        p.terminate()
                        p.wait(5)
                except (psutil.Error, OSError):
                    pass

    def start(self, device: str | None = None) -> dict:
        """Arranca ace-server y espera a que responda. Si ya está lanzado (quizá por otro
        hilo), no lo relanza: sólo espera a que termine de arrancar."""
        with self._lock:
            if not (self.proc and self.proc.poll() is None):
                failed = self._launch(device)
                if failed is not None:
                    return failed
        return self._wait_ready()  # sin el lock, para no bloquear stop() mientras arranca

    def _launch(self, device: str | None) -> dict | None:
        """Lanza el proceso (con el lock tomado). Devuelve el estado si falla, None si arrancó."""
        cfg = self.settings.engine
        device = device or cfg["device"]
        exe = self.binary_path(cfg)
        if not exe.exists():
            self.state, self.error = "error", f"No se encuentra {exe}. Descarga el motor en la sección Modelos."
            return self.status()

        self._kill_orphan_on_port(cfg["port"])
        cmd, env = self.build_command(cfg, device)
        self.backend = None
        self.cpu_threads = None
        self.error = None
        self.state = "starting"
        self.launch_cfg = dict(cfg, device=device)
        self._log(f"[Open Suno] Iniciando motor (dispositivo: {device}) → {' '.join(cmd)}")
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                cwd=str(resolve(cfg["bin_dir"])),
                creationflags=_NO_WINDOW,
                preexec_fn=_die_with_parent if not IS_WINDOWS else None,  # noqa: PLW1509
            )
        except OSError as e:
            self.state, self.error = "error", f"No se pudo lanzar ace-server: {e}"
            self.proc = None
            return self.status()
        self.started_at = time.time()
        _JOB.assign(self.proc.pid)
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
        return None

    def _wait_ready(self, timeout: float = 60) -> dict:
        """Espera (fuera del lock) a que /health responda, a que el proceso muera o a que lo detengan."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.state == "running":
                return self.status()
            if self.state in ("stopping", "stopped"):  # lo detuvieron mientras arrancaba
                return self.status()
            proc = self.proc
            if proc is None or proc.poll() is not None:
                with self._lock:
                    if self.state == "starting":
                        self.state = "error"
                        tail = [l for _, _, l in list(self.logs)[-8:]]
                        self.error = "El motor se cerró al arrancar. " + " | ".join(tail[-3:])
                return self.status()
            try:
                r = httpx.get(self.base_url() + "/health", timeout=2)
                if r.status_code == 200:
                    with self._lock:
                        if self.state == "starting":
                            self.state = "running"
                            self._log("[Open Suno] Motor listo en " + self.base_url())
                    return self.status()
            except httpx.HTTPError:
                pass
            time.sleep(0.4)
        with self._lock:
            if self.state == "starting":
                self.state, self.error = "error", f"El motor no respondió a /health en {int(timeout)} s."
        return self.status()

    def _reader(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        try:
            if self._log_file.exists() and self._log_file.stat().st_size > 5 * 2**20:
                self._log_file.replace(self._log_file.with_suffix(".log.1"))  # rotación simple
        except OSError:
            pass
        try:
            with open(self._log_file, "a", encoding="utf-8", errors="replace") as fh:
                for raw in iter(proc.stdout.readline, b""):
                    line = raw.decode("utf-8", errors="replace")
                    if m := _RE_VERSION.search(line):
                        self.version = m.group(1)
                    fh.write(line.rstrip("\r\n") + "\n")
                    fh.flush()
                    self._log(line)
        except (OSError, ValueError):
            pass
        code = proc.wait()
        with self._lock:
            if self.proc is proc:
                if self.state not in ("stopping", "stopped"):
                    self.state = "error"
                    self.error = f"El motor terminó inesperadamente (código {code})."
                self.proc = None
        self._log(f"[Open Suno] Proceso del motor finalizado (código {code})")

    def stop(self) -> dict:
        with self._lock:
            proc = self.proc
            if not proc or proc.poll() is not None:
                self.proc = None
                self.state = "stopped"
                return self.status()
            self.state = "stopping"
            self._log("[Open Suno] Deteniendo motor…")
        try:
            proc.terminate()
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(5)
        with self._lock:
            self.proc = None
            self.state = "stopped"
            self.started_at = None
        return self.status()

    def restart(self, device: str | None = None) -> dict:
        self.stop()
        return self.start(device)

    def ensure_running(self) -> bool:
        if self.is_running():
            return True
        st = self.start()  # si otro hilo ya lo está arrancando, start() espera
        return st["state"] == "running"

    def is_running(self) -> bool:
        return self.state == "running" and self.proc is not None and self.proc.poll() is None

    # --------------------------------------------------------------- estado
    def needs_restart(self) -> bool:
        if not self.is_running() or not self.launch_cfg:
            return False
        cfg = self.settings.engine
        return any(cfg[k] != self.launch_cfg.get(k) for k in RESTART_KEYS)

    def status(self) -> dict:
        proc = self.proc
        mem = None
        if proc and proc.poll() is None:
            try:
                mem = round(psutil.Process(proc.pid).memory_info().rss / 2**20)
            except psutil.Error:
                pass
        return {
            "state": self.state,
            "error": self.error,
            "pid": proc.pid if proc and proc.poll() is None else None,
            "url": self.base_url(),
            "uptime": (time.time() - self.started_at) if self.started_at and self.state == "running" else None,
            "device": (self.launch_cfg or {}).get("device"),
            "backend": self.backend,
            "cpu_threads": self.cpu_threads,
            "version": self.version or self.binary_version(),
            "ram_mb": mem,
            "needs_restart": self.needs_restart(),
            "binary": str(self.binary_path()),
            "binary_exists": self.binary_path().exists(),
        }

    def props(self) -> dict | None:
        if not self.is_running():
            return None
        try:
            r = httpx.get(self.base_url() + "/props", timeout=5)
            return r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None
