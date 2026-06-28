from __future__ import annotations

import base64
import json
import os
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

MIMIR_DATA_DIR = Path(os.environ.get("MIMIR_DATA_DIR", Path.cwd() / ".mimir-data"))
os.environ.setdefault("YOLO_CONFIG_DIR", str(MIMIR_DATA_DIR / "ultralytics"))
os.environ.setdefault("MPLCONFIGDIR", str(MIMIR_DATA_DIR / "matplotlib"))
Path(os.environ["YOLO_CONFIG_DIR"]).mkdir(parents=True, exist_ok=True)
Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)

import cv2
import requests
from ultralytics import YOLO


PERSON = 0
VEHICLES = {2, 5, 7}

MIN_CONF = 0.40
MIN_AREA_RATIO = 0.012
SAMPLE_FPS = 2.0
EVENT_TRIGGER = 14.0
EVENT_END_TIMEOUT = 2.0
MIN_EVENT_FRAMES = 4
IGNORE_TOP_RATIO = 0.20

PRIORITY_LABELS = {
    0: "IGNORE",
    1: "REVIEW",
    2: "IMPORTANT",
}


@dataclass
class MimirSettings:
    incoming_dir: Path
    output_dir: Path
    model_path: Path = Path("yolov8n.pt")
    ai_enabled: bool = False
    ollama_model: str = "llava:7b"
    move_source_files: bool = True


@dataclass
class EventResult:
    video: str
    event_id: int
    label: str
    confidence: int
    reason: str
    findings: list[str]
    recommendation: str
    analysis_method: str
    score: float
    active_frames: int
    persons: int
    vehicles: int
    evidence_frame: str | None


@dataclass
class ScanSummary:
    total_videos: int
    important: int
    review: int
    ignore: int
    events: list[EventResult]
    report_path: str


ProgressCallback = Callable[[str, int, int], None]
LogCallback = Callable[[str], None]


class MimirScanner:
    def __init__(
        self,
        settings: MimirSettings,
        on_progress: ProgressCallback | None = None,
        on_log: LogCallback | None = None,
    ) -> None:
        self.settings = settings
        self.on_progress = on_progress or (lambda _message, _current, _total: None)
        self.on_log = on_log or (lambda _message: None)
        self.important_dir = settings.output_dir / "Important"
        self.review_dir = settings.output_dir / "Review"
        self.ignore_dir = settings.output_dir / "Ignore"
        self.frames_dir = settings.output_dir / "Frames"
        self.report_dir = settings.output_dir / "Reports"

        for folder in [
            self.important_dir,
            self.review_dir,
            self.ignore_dir,
            self.frames_dir,
            self.report_dir,
        ]:
            folder.mkdir(parents=True, exist_ok=True)

        self.on_log("Loading local vision model...")
        self.yolo = YOLO(str(settings.model_path))
        self.on_log("Model ready.")

    def scan(self) -> ScanSummary:
        videos = list(find_videos(self.settings.incoming_dir))
        decisions: dict[Path, int] = {}
        events: list[EventResult] = []

        for index, video in enumerate(videos, start=1):
            self.on_progress(video.name, index - 1, len(videos))
            self.on_log(f"Scanning {video.name}")
            self._process_video(video, decisions, events)
            self.on_progress(video.name, index, len(videos))

        if self.settings.move_source_files:
            self._move_files(decisions)

        summary = self._write_report(videos, decisions, events)
        self.on_log(f"Report saved to {summary.report_path}")
        return summary

    def _process_video(
        self,
        path: Path,
        decisions: dict[Path, int],
        events: list[EventResult],
    ) -> None:
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            self.on_log(f"Could not open {path.name}; sending to Ignore.")
            decisions[path] = 0
            return

        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        step = max(1, int(fps / SAMPLE_FPS))
        frame_i = 0
        rolling = 0.0
        active = False
        event_score = 0.0
        active_frames = 0
        persons = 0
        vehicles = 0
        best_frame = None
        best_score = 0.0
        last_activity = 0.0
        event_id = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_i % step != 0:
                frame_i += 1
                continue

            score, p, v = self._analyze_frame(frame)
            rolling = rolling * 0.85 + score
            now = time.time()

            if not active and rolling > EVENT_TRIGGER:
                active = True
                event_score = 0.0
                active_frames = 0
                persons = 0
                vehicles = 0
                best_frame = None
                best_score = 0.0
                self.on_log(f"Event {event_id} started in {path.name}")

            if active:
                active_frames += 1
                event_score += score
                persons += p
                vehicles += v

                if score > best_score:
                    best_score = score
                    best_frame = frame.copy()

                if score > 0:
                    last_activity = now

                if last_activity and now - last_activity > EVENT_END_TIMEOUT:
                    event = self._finalize_event(
                        path,
                        event_id,
                        best_frame,
                        event_score,
                        persons,
                        vehicles,
                        active_frames,
                    )
                    if event:
                        events.append(event)
                        decisions[path] = max(decisions.get(path, 0), label_priority(event.label))
                    active = False
                    rolling = 0.0
                    event_id += 1
                    last_activity = 0.0

            frame_i += 1

        if active and best_frame is not None:
            event = self._finalize_event(
                path,
                event_id,
                best_frame,
                event_score,
                persons,
                vehicles,
                active_frames,
            )
            if event:
                events.append(event)
                decisions[path] = max(decisions.get(path, 0), label_priority(event.label))

        decisions.setdefault(path, 0)
        cap.release()

    def _analyze_frame(self, frame) -> tuple[float, int, int]:
        h, w = frame.shape[:2]
        frame = frame[int(h * IGNORE_TOP_RATIO) :, :]
        h, w = frame.shape[:2]
        results = self.yolo(frame, verbose=False)
        score = 0.0
        persons = 0
        vehicles = 0

        for result in results:
            for box in result.boxes:
                conf = float(box.conf[0])
                if conf < MIN_CONF:
                    continue

                cls = int(box.cls[0])
                x1, y1, x2, y2 = box.xyxy[0]
                area = float((x2 - x1) * (y2 - y1))
                area_ratio = area / (w * h)
                if area_ratio < MIN_AREA_RATIO:
                    continue

                prox = proximity_bonus(box, w, h)
                if cls == PERSON:
                    persons += 1
                    score += prox * 12 + area_ratio * 50
                    if conf > 0.75:
                        score += 2
                elif cls in VEHICLES:
                    vehicles += 1
                    score += prox * 1.5 + area_ratio * 6
                    if conf > 0.75:
                        score += 1

        return score, persons, vehicles

    def _finalize_event(
        self,
        path: Path,
        event_id: int,
        best_frame,
        event_score: float,
        persons: int,
        vehicles: int,
        active_frames: int,
    ) -> EventResult | None:
        if best_frame is None or active_frames < MIN_EVENT_FRAMES:
            return None

        frame_path = self.frames_dir / f"{path.name}_event_{event_id}.jpg"
        cv2.imwrite(str(frame_path), best_frame)

        ai_label = self._run_ai(frame_path) if self.settings.ai_enabled else None
        label, confidence, reason, findings, recommendation = explain_event(
            event_score=event_score,
            persons=persons,
            vehicles=vehicles,
            active_frames=active_frames,
            ai_label=ai_label,
        )

        self.on_log(
            f"{label}: event {event_id} in {path.name} "
            f"(score={event_score:.1f}, people={persons}, vehicles={vehicles})"
        )
        return EventResult(
            video=str(path),
            event_id=event_id,
            label=label,
            confidence=confidence,
            reason=reason,
            findings=findings,
            recommendation=recommendation,
            analysis_method="Object detection + local AI" if self.settings.ai_enabled else "Object detection",
            score=event_score,
            active_frames=active_frames,
            persons=persons,
            vehicles=vehicles,
            evidence_frame=str(frame_path),
        )

    def _run_ai(self, image_path: Path) -> str:
        if not self.settings.ai_enabled:
            return "REVIEW"

        prompt = """
You are analyzing Tesla sentry footage.

Your task is to determine whether this event is important.

Return ONLY one word:
IMPORTANT
REVIEW
IGNORE

IMPORTANT:
- someone touching Tesla
- suspicious interaction near Tesla
- lingering near Tesla
- possible vandalism
- someone inspecting windows/doors
- person very close to Tesla

REVIEW:
- nearby people
- nearby vehicles
- uncertain activity
- something worth quickly checking

IGNORE:
- normal traffic
- distant vehicles
- harmless movement
- empty parking lot
- nothing interacting with Tesla

Be conservative.
Most clips should be IGNORE.
""".strip()

        try:
            img = base64.b64encode(image_path.read_bytes()).decode()
            response = requests.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": self.settings.ollama_model,
                    "prompt": prompt,
                    "images": [img],
                    "stream": False,
                },
                timeout=60,
            )
            label = response.json().get("response", "IGNORE").strip().upper()
            return label if label in {"IMPORTANT", "REVIEW", "IGNORE"} else "IGNORE"
        except Exception as exc:
            self.on_log(f"Local AI unavailable: {exc}")
            return "IGNORE"

    def _move_files(self, decisions: dict[Path, int]) -> None:
        for source, priority in decisions.items():
            destination_dir = {
                2: self.important_dir,
                1: self.review_dir,
                0: self.ignore_dir,
            }[priority]
            destination = unique_path(destination_dir / source.name)
            try:
                shutil.move(str(source), str(destination))
            except Exception as exc:
                self.on_log(f"Move failed for {source.name}: {exc}")

    def _write_report(
        self,
        videos: list[Path],
        decisions: dict[Path, int],
        events: list[EventResult],
    ) -> ScanSummary:
        counts = {"IMPORTANT": 0, "REVIEW": 0, "IGNORE": 0}
        for priority in decisions.values():
            counts[PRIORITY_LABELS[priority]] += 1

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        report_path = self.report_dir / f"mimir-report-{timestamp}.json"
        summary = ScanSummary(
            total_videos=len(videos),
            important=counts["IMPORTANT"],
            review=counts["REVIEW"],
            ignore=counts["IGNORE"],
            events=events,
            report_path=str(report_path),
        )
        report_path.write_text(
            json.dumps(asdict(summary), indent=2),
            encoding="utf-8",
        )
        return summary


def find_videos(folder: Path) -> Iterable[Path]:
    for root, _dirs, files in os.walk(folder):
        for name in files:
            if name.lower().endswith((".mp4", ".mov", ".avi", ".mkv")):
                yield Path(root) / name


def label_priority(label: str) -> int:
    return {"IGNORE": 0, "REVIEW": 1, "IMPORTANT": 2}.get(label, 0)


def explain_event(
    event_score: float,
    persons: int,
    vehicles: int,
    active_frames: int,
    ai_label: str | None,
) -> tuple[str, int, str, list[str], str]:
    findings: list[str] = []
    if persons:
        findings.append(f"Detected people in {persons} sampled frame{'s' if persons != 1 else ''}.")
    if vehicles:
        findings.append(f"Detected nearby vehicles in {vehicles} sampled frame{'s' if vehicles != 1 else ''}.")
    findings.append(f"Activity lasted across {active_frames} sampled frames.")

    if ai_label in {"IMPORTANT", "REVIEW", "IGNORE"}:
        label = ai_label
    elif persons and event_score >= EVENT_TRIGGER * 2.6:
        label = "IMPORTANT"
    elif persons or event_score >= EVENT_TRIGGER * 1.2:
        label = "REVIEW"
    else:
        label = "IGNORE"

    if ai_label == "IGNORE" and event_score >= EVENT_TRIGGER * 1.8 and persons:
        label = "REVIEW"

    strength = min(1.0, event_score / (EVENT_TRIGGER * 4.0))
    duration_bonus = min(0.18, active_frames / 80)
    people_bonus = 0.18 if persons else 0.0
    vehicle_bonus = 0.08 if vehicles else 0.0
    confidence = round((0.46 + strength * 0.28 + duration_bonus + people_bonus + vehicle_bonus) * 100)
    confidence = max(55, min(confidence, 96))

    if label == "IMPORTANT":
        reason = "Mimir saw close or sustained person activity near the vehicle."
        recommendation = "Open this clip first and check whether anyone touched, inspected, or lingered around the car."
    elif label == "REVIEW":
        reason = "Mimir saw activity near the vehicle that may be harmless, but is worth a quick look."
        recommendation = "Review this clip when you have a moment. It is not marked as urgent."
    else:
        reason = "Mimir saw limited activity and no strong sign of interaction with the vehicle."
        recommendation = "No action is likely needed unless you want to audit the full recording."

    if ai_label:
        findings.append(f"Local AI classified the evidence frame as {ai_label.lower()}.")

    return label, confidence, reason, findings, recommendation


def proximity_bonus(box, frame_width: int, frame_height: int) -> float:
    x1, y1, x2, y2 = box.xyxy[0]
    center_x = float((x1 + x2) / 2)
    center_y = float((y1 + y2) / 2)
    dx = abs(center_x - frame_width / 2) / (frame_width / 2)
    dy = abs(center_y - frame_height / 2) / (frame_height / 2)
    return max(0.0, 1.0 - ((dx + dy) / 2))


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    index = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1
