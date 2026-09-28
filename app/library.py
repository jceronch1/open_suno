"""Biblioteca de canciones generadas: data/library/<id>/{meta.json, audio.*, latent.vae}."""

from __future__ import annotations

import json
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

from .paths import LIBRARY

LATENT_FRAME_BYTES = 64 * 4  # [T, 64] f32
SAMPLES_PER_FRAME = 1920  # 48 kHz / 25 Hz

_TAG_LINE = re.compile(r"^\s*\[.*\]\s*$")


def make_title(lyrics: str, caption: str) -> str:
    """Primera línea cantada de la letra o, si es instrumental, el inicio de la descripción."""
    if lyrics and lyrics.strip().lower() != "[instrumental]":
        for line in lyrics.splitlines():
            line = line.strip()
            if line and not _TAG_LINE.match(line):
                # Quita marcas de Markdown y emojis iniciales («# 🎵 Título» → «Título»).
                line = re.sub(r"^[\s#*>_~`\-]+", "", line)
                line = re.sub(r"^[^\w¡¿\"“]+", "", line)
                line = re.sub(r"[\"“”*_`]", "", line).strip(" ,.;:!?¡¿-")
                if line:
                    return line[:48]
    words = re.sub(r"\s+", " ", caption or "").strip(" '\"`“”‘’").split(" ")
    title = " ".join(words[:6]).strip(" ,.;:'\"`“”‘’")
    return (title[:48] or "Canción sin título").capitalize()


class Library:
    def __init__(self) -> None:
        LIBRARY.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._cache: dict[str, dict] | None = None

    # ---------------------------------------------------------------- lectura
    def _scan(self) -> dict[str, dict]:
        songs: dict[str, dict] = {}
        for d in LIBRARY.iterdir():
            meta_file = d / "meta.json"
            if d.is_dir() and meta_file.exists():
                try:
                    songs[d.name] = json.loads(meta_file.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
        return songs

    def _all(self) -> dict[str, dict]:
        with self._lock:
            if self._cache is None:
                self._cache = self._scan()
            return self._cache

    def list(self, q: str = "", favorites: bool = False, limit: int = 0) -> list[dict]:
        songs = sorted(self._all().values(), key=lambda s: s.get("created_at", 0), reverse=True)
        if favorites:
            songs = [s for s in songs if s.get("favorite")]
        if q:
            ql = q.lower()
            songs = [
                s
                for s in songs
                if ql in " ".join(
                    (s.get("title", ""), s.get("request", {}).get("caption", ""), s.get("request", {}).get("lyrics", ""))
                ).lower()
            ]
        return songs[:limit] if limit else songs

    def get(self, song_id: str) -> dict | None:
        return self._all().get(song_id)

    def folder(self, song_id: str) -> Path:
        if not re.fullmatch(r"[\w-]+", song_id):
            raise ValueError("id inválido")
        return LIBRARY / song_id

    def audio_path(self, song_id: str) -> Path | None:
        meta = self.get(song_id)
        if not meta:
            return None
        p = self.folder(song_id) / Path(str(meta.get("file", ""))).name  # nunca salir de su carpeta
        return p if p.exists() else None

    def latent_path(self, song_id: str) -> Path | None:
        p = self.folder(song_id) / "latent.vae"
        return p if p.exists() else None

    # --------------------------------------------------------------- escritura
    def add(self, audio: bytes, mime: str, latent: bytes | None, meta: dict) -> dict:
        song_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        folder = self.folder(song_id)
        folder.mkdir(parents=True)
        ext = "wav" if "wav" in mime else "mp3"
        (folder / f"audio.{ext}").write_bytes(audio)
        duration = None
        if latent:
            (folder / "latent.vae").write_bytes(latent)
            duration = round(len(latent) / LATENT_FRAME_BYTES * SAMPLES_PER_FRAME / 48000, 2)
        meta = dict(meta)
        meta.update(
            {
                "id": song_id,
                "file": f"audio.{ext}",
                "format": ext,
                "size": len(audio),
                "duration": duration or meta.get("request", {}).get("duration"),
                "has_latent": bool(latent),
                "created_at": time.time(),
                "favorite": False,
            }
        )
        self._write_meta(song_id, meta)
        return meta

    def _write_meta(self, song_id: str, meta: dict) -> None:
        folder = self.folder(song_id)
        tmp = folder / "meta.tmp"
        tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(folder / "meta.json")
        with self._lock:
            if self._cache is not None:
                self._cache[song_id] = meta

    def update(self, song_id: str, patch: dict) -> dict | None:
        meta = self.get(song_id)
        if not meta:
            return None
        meta = dict(meta)
        if "title" in patch:
            meta["title"] = str(patch["title"]).strip()[:120] or meta["title"]
        if "favorite" in patch:
            meta["favorite"] = bool(patch["favorite"])
        self._write_meta(song_id, meta)
        return meta

    def delete(self, song_id: str) -> bool:
        folder = self.folder(song_id)
        if not folder.exists():
            return False
        shutil.rmtree(folder, ignore_errors=True)
        with self._lock:
            if self._cache is not None:
                self._cache.pop(song_id, None)
        return True

    def stats(self) -> dict:
        songs = self._all().values()
        return {
            "count": len(songs),
            "duration": round(sum((s.get("duration") or 0) for s in songs)),
            "size": sum((s.get("size") or 0) for s in songs),
        }
