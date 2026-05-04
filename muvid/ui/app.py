"""FastAPI app — thin wrapper around the muvid facade.

Single project per process; the project root is bound when the app is
created (via ``serve(root=...)``). The frontend at ``GET /`` is a
single static HTML page that polls ``/api/status`` and offers buttons
for each pipeline stage.

This is **not** a multi-tenant SaaS. It's a localhost dev tool.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from typing import Any, Optional


_PROJECT_ROOT: Optional[Path] = None
_LOG_LOCK = threading.Lock()
_LOG_LINES: list[str] = []


def _log(msg: str) -> None:
    line = f"[{_now()}] {msg}"
    with _LOG_LOCK:
        _LOG_LINES.append(line)
        if len(_LOG_LINES) > 500:
            del _LOG_LINES[: len(_LOG_LINES) - 500]


def _now() -> str:
    import datetime as _dt

    return _dt.datetime.now().strftime("%H:%M:%S")


def create_app(root: str | Path):
    try:
        from fastapi import FastAPI, HTTPException  # type: ignore
        from fastapi.responses import HTMLResponse, FileResponse, PlainTextResponse  # type: ignore
        from fastapi.staticfiles import StaticFiles  # type: ignore
        from pydantic import BaseModel  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "muvid UI requires `fastapi` and `uvicorn`. "
            "pip install fastapi uvicorn pydantic"
        ) from e

    global _PROJECT_ROOT
    _PROJECT_ROOT = Path(root).expanduser().resolve()

    from muvid import facade

    app = FastAPI(title="muvid", description="Music video pipeline UI")

    static_dir = Path(__file__).parent / "static"

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (static_dir / "index.html").read_text()

    @app.get("/api/status")
    def status():
        return facade.status(str(_PROJECT_ROOT))

    @app.get("/api/log", response_class=PlainTextResponse)
    def log():
        with _LOG_LOCK:
            return "\n".join(_LOG_LINES[-200:])

    class TranscribeReq(BaseModel):
        api_key: Optional[str] = None

    @app.post("/api/transcribe")
    def transcribe(req: TranscribeReq):
        _log("transcribe: starting")
        try:
            out = facade.transcribe_song(str(_PROJECT_ROOT), api_key=req.api_key)
            _log(f"transcribe: ok → {out}")
            return {"ok": True, "lyrics_md": out}
        except Exception as e:
            _log(f"transcribe: ERROR {e}")
            raise HTTPException(500, str(e))

    class AlignReq(BaseModel):
        pass

    @app.post("/api/align")
    def align(_: AlignReq):
        _log("align: starting")
        try:
            out = facade.align_lyrics(str(_PROJECT_ROOT))
            _log(f"align: ok → {out}")
            return {"ok": True, "alignment": out}
        except Exception as e:
            _log(f"align: ERROR {e}")
            raise HTTPException(500, str(e))

    class CharacterReq(BaseModel):
        name: str
        description: str = ""
        voice_id: str = ""
        reference_audio_url: str = ""

    @app.post("/api/character")
    def character(req: CharacterReq):
        out = facade.add_character(
            str(_PROJECT_ROOT),
            req.name,
            description=req.description,
            voice_id=req.voice_id,
            reference_audio_url=req.reference_audio_url,
        )
        _log(f"character: upserted {req.name!r}")
        return out

    class GenerateImagesReq(BaseModel):
        name: str
        n: int = 6
        quality: str = "balanced"

    @app.post("/api/character/generate")
    def character_generate(req: GenerateImagesReq):
        _log(f"character.generate: {req.name} n={req.n}")
        try:
            out = facade.generate_character_images(
                str(_PROJECT_ROOT),
                req.name,
                n=req.n,
                quality=req.quality,
            )
            _log(f"character.generate: ok ({len(out)})")
            return {"ok": True, "paths": out}
        except Exception as e:
            _log(f"character.generate: ERROR {e}")
            raise HTTPException(500, str(e))

    class CurateReq(BaseModel):
        name: str
        k: int = 8
        recipe: str = "person_mock"

    @app.post("/api/character/curate")
    def character_curate(req: CurateReq):
        _log(f"character.curate: {req.name} k={req.k}")
        try:
            out = facade.curate_character(
                str(_PROJECT_ROOT),
                req.name,
                k=req.k,
                recipe=req.recipe,
            )
            _log(f"character.curate: ok ({len(out)})")
            return {"ok": True, "paths": out}
        except Exception as e:
            _log(f"character.curate: ERROR {e}")
            raise HTTPException(500, str(e))

    class EnvironmentReq(BaseModel):
        name: str
        description: str = ""
        time_of_day: str = ""
        lighting: str = ""

    @app.post("/api/environment")
    def environment(req: EnvironmentReq):
        out = facade.add_environment(
            str(_PROJECT_ROOT),
            req.name,
            description=req.description,
            time_of_day=req.time_of_day,
            lighting=req.lighting,
        )
        _log(f"environment: upserted {req.name!r}")
        return out

    @app.post("/api/environment/{name}/render")
    def environment_render(name: str):
        _log(f"environment.render: {name}")
        try:
            out = facade.render_environment(str(_PROJECT_ROOT), name)
            _log(f"environment.render: ok → {out}")
            return {"ok": True, "path": out}
        except Exception as e:
            _log(f"environment.render: ERROR {e}")
            raise HTTPException(500, str(e))

    @app.get("/api/script")
    def get_script():
        path = _PROJECT_ROOT / "script" / "script.md"
        if not path.exists():
            facade.write_script(str(_PROJECT_ROOT))
        return {"path": str(path), "content": path.read_text() if path.exists() else ""}

    class ScriptReq(BaseModel):
        content: str

    @app.post("/api/script")
    def set_script(req: ScriptReq):
        path = _PROJECT_ROOT / "script" / "script.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(req.content)
        facade.parse_script(str(_PROJECT_ROOT))
        _log("script: applied")
        return {"ok": True, "path": str(path)}

    class RenderReq(BaseModel):
        shot_id: Optional[str] = None
        quality: str = "balanced"
        force: bool = False

    @app.post("/api/render")
    def render(req: RenderReq):
        _log(f"render: shot={req.shot_id} quality={req.quality} force={req.force}")
        try:
            if req.shot_id:
                p = facade.render_shot(
                    str(_PROJECT_ROOT),
                    req.shot_id,
                    quality=req.quality,
                    force=req.force,
                )
                _log(f"render: shot ok → {p}")
                return {"ok": True, "paths": [p]}
            paths = facade.render(
                str(_PROJECT_ROOT),
                quality=req.quality,
                force=req.force,
            )
            _log(f"render: all ok ({len(paths)})")
            return {"ok": True, "paths": paths}
        except Exception as e:
            _log(f"render: ERROR {e}")
            raise HTTPException(500, str(e))

    class ComposeReq(BaseModel):
        out_name: str = "final.mp4"
        use_song_audio: bool = True

    @app.post("/api/compose")
    def compose(req: ComposeReq):
        _log("compose: starting")
        try:
            out = facade.compose(
                str(_PROJECT_ROOT),
                out_name=req.out_name,
                use_song_audio=req.use_song_audio,
            )
            _log(f"compose: ok → {out}")
            return {"ok": True, "path": out}
        except Exception as e:
            _log(f"compose: ERROR {e}")
            raise HTTPException(500, str(e))

    @app.get("/api/file")
    def file(path: str):
        """Serve a file from inside the project root (sandboxed)."""
        target = (_PROJECT_ROOT / path).resolve()
        if not str(target).startswith(str(_PROJECT_ROOT)):
            raise HTTPException(403, "Path escapes project root")
        if not target.exists():
            raise HTTPException(404, "Not found")
        return FileResponse(str(target))

    return app


def serve(*, root: str | Path = ".", host: str = "127.0.0.1", port: int = 7800) -> None:
    """Run the UI on localhost via uvicorn."""
    try:
        import uvicorn  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "muvid UI requires `uvicorn`. pip install uvicorn[standard]"
        ) from e
    app = create_app(root)
    uvicorn.run(app, host=host, port=port, log_level="info")
