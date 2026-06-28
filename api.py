"""
Mimir FastAPI server.

This bridges the desktop UI to the local scanner in mimir_core.py.
"""
from dataclasses import asdict
from pathlib import Path
from typing import Optional
import threading

import anyio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from mimir_core import MimirScanner, MimirSettings


app = FastAPI(title="Mimir API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ScanRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    incoming_dir: str
    output_dir: Optional[str] = None
    model_path: Optional[str] = None
    ai_enabled: bool = False
    move_files: bool = True


current_scan_progress = {"message": "", "current": 0, "total": 0}
current_scan_lock = threading.Lock()
servable_frame_dirs: set[Path] = set()
servable_video_dirs: set[Path] = set()


def set_progress(message: str, current: int, total: int) -> None:
    current_scan_progress.update(
        {"message": message, "current": current, "total": total}
    )


def default_model_path() -> Path:
    candidates = [
        Path.cwd() / "yolov8n.pt",
        Path(__file__).resolve().parent / "yolov8n.pt",
        Path("C:/OG_TeslaAI/yolov8n.pt"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


@app.get("/health")
async def health():
    return {"status": "ok", "service": "Mimir API"}


@app.post("/scan")
async def start_scan(request: ScanRequest):
    incoming = Path(request.incoming_dir).expanduser()
    output = Path(request.output_dir or Path.home() / "Documents" / "Mimir").expanduser()
    model = Path(request.model_path).expanduser() if request.model_path else default_model_path()

    if not incoming.is_dir():
        raise HTTPException(status_code=400, detail="That footage folder could not be found.")
    if not model.is_file():
        raise HTTPException(status_code=400, detail="The local vision model could not be found.")

    if current_scan_lock.locked():
        raise HTTPException(status_code=409, detail="A scan is already running.")

    if not current_scan_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A scan is already running.")

    try:
        set_progress("Preparing scan", 0, 0)
        settings = MimirSettings(
            incoming_dir=incoming,
            output_dir=output,
            model_path=model,
            ai_enabled=request.ai_enabled,
            move_source_files=request.move_files,
        )
        scanner = MimirScanner(settings, on_progress=set_progress)
        servable_frame_dirs.add(scanner.frames_dir.resolve())
        servable_video_dirs.add(incoming.resolve())
        summary = await anyio.to_thread.run_sync(scanner.scan)
        set_progress("Scan completed", summary.total_videos, summary.total_videos)

        return {
            "status": "completed",
            "total_videos": summary.total_videos,
            "important": summary.important,
            "review": summary.review,
            "ignore": summary.ignore,
            "events": [asdict(event) for event in summary.events],
            "report_path": summary.report_path,
        }
    except Exception as exc:
        set_progress("Scan failed", 0, 0)
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        current_scan_lock.release()


@app.get("/progress")
async def get_progress():
    return current_scan_progress


@app.get("/frame")
async def get_frame(path: str):
    frame_path = Path(path).expanduser().resolve()
    if not frame_path.is_file():
        raise HTTPException(status_code=404, detail="Frame not found")

    if not any(
        frame_path == frame_dir or frame_dir in frame_path.parents
        for frame_dir in servable_frame_dirs
    ):
        raise HTTPException(status_code=403, detail="Frame is outside the active scan output")

    return FileResponse(frame_path)


@app.get("/video")
async def get_video(path: str):
    video_path = Path(path).expanduser().resolve()
    if not video_path.is_file():
        raise HTTPException(status_code=404, detail="Video not found")

    if video_path.suffix.lower() not in {".mp4", ".mov", ".m4v", ".avi", ".mkv"}:
        raise HTTPException(status_code=400, detail="Unsupported video type")

    if not any(
        video_path == video_dir or video_dir in video_path.parents
        for video_dir in servable_video_dirs
    ):
        raise HTTPException(status_code=403, detail="Video is outside the active scan folder")

    return FileResponse(video_path)


if __name__ == "__main__":
    import uvicorn

    print("Mimir API running on http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
