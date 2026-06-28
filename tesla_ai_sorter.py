id="hz27zy"
import os
import cv2
import shutil
import time
import base64
import json
import requests
from datetime import datetime

from ultralytics import YOLO
from config import (
    FRAMES,
    IGNORE,
    IMPORTANT,
    INCIDENTS_OUTPUT,
    INCOMING,
    LATEST_SESSION_JSON,
    LLM_MODEL,
    MIMIR_OUTPUT,
    REVIEW,
    YOLO_MODEL,
)

from rich.console import Console
from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn
)
from rich.table import Table
from rich.panel import Panel

# =========================================================
# CONSOLE
# =========================================================

console = Console()

# =========================================================
# CONFIG
# =========================================================

# YOLO classes
PERSON = 0
VEHICLES = {2, 5, 7}

# detection tuning
MIN_CONF = 0.40
MIN_AREA_RATIO = 0.012

# sampling
SAMPLE_FPS = 2.0

# event logic
EVENT_TRIGGER = 14.0
EVENT_END_TIMEOUT = 2.0
MIN_EVENT_FRAMES = 4

# AI
AI_ENABLED = True

# crop top area (distant traffic/sky)
IGNORE_TOP_RATIO = 0.20

# =========================================================
# CREATE FOLDERS
# =========================================================

for f in [
    INCOMING,
    IMPORTANT,
    REVIEW,
    IGNORE,
    FRAMES,
    MIMIR_OUTPUT,
    INCIDENTS_OUTPUT,
]:
    os.makedirs(f, exist_ok=True)

# =========================================================
# LOAD YOLO
# =========================================================

console.print("[bold cyan]Loading YOLO...[/bold cyan]")

yolo = YOLO(YOLO_MODEL)

console.print("[bold green]YOLO loaded.[/bold green]")

# =========================================================
# AI
# =========================================================

def run_ai(image_path):

    if not AI_ENABLED:
        return "IGNORE"

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

        with open(image_path, "rb") as f:
            img = base64.b64encode(f.read()).decode()

        r = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": LLM_MODEL,
                "prompt": prompt,
                "images": [img],
                "stream": False
            },
            timeout=60
        )

        response = (
            r.json()
            .get("response", "IGNORE")
            .strip()
            .upper()
        )

        if response not in [
            "IMPORTANT",
            "REVIEW",
            "IGNORE"
        ]:
            return "IGNORE"

        return response

    except Exception as e:

        console.print(
            f"[red]AI ERROR:[/red] {e}"
        )

        return "IGNORE"

# =========================================================
# PROXIMITY
# =========================================================

def proximity_bonus(box, frame_width, frame_height):

    x1, y1, x2, y2 = box.xyxy[0]

    center_x = (x1 + x2) / 2
    center_y = (y1 + y2) / 2

    dx = abs(
        center_x - frame_width / 2
    ) / (frame_width / 2)

    dy = abs(
        center_y - frame_height / 2
    ) / (frame_height / 2)

    distance = (dx + dy) / 2

    return max(0, 1.0 - distance)

# =========================================================
# ANALYZE FRAME
# =========================================================

def analyze(frame):

    h, w = frame.shape[:2]

    # crop away top traffic area
    frame = frame[int(h * IGNORE_TOP_RATIO):, :]

    h, w = frame.shape[:2]

    results = yolo(frame, verbose=False)

    score = 0

    persons = 0
    vehicles = 0

    for r in results:

        for box in r.boxes:

            conf = float(box.conf[0])

            if conf < MIN_CONF:
                continue

            cls = int(box.cls[0])

            x1, y1, x2, y2 = box.xyxy[0]

            area = float(
                (x2 - x1) *
                (y2 - y1)
            )

            area_ratio = area / (w * h)

            # ignore tiny distant detections
            if area_ratio < MIN_AREA_RATIO:
                continue

            prox = proximity_bonus(box, w, h)

            # =================================================
            # PERSON
            # =================================================

            if cls == PERSON:

                persons += 1

                person_score = 0

                # people matter heavily
                person_score += prox * 12

                person_score += area_ratio * 50

                if conf > 0.75:
                    person_score += 2

                score += person_score

            # =================================================
            # VEHICLES
            # =================================================

            elif cls in VEHICLES:

                vehicles += 1

                vehicle_score = 0

                # vehicles matter much less
                vehicle_score += prox * 1.5

                vehicle_score += area_ratio * 6

                if conf > 0.75:
                    vehicle_score += 1

                score += vehicle_score

    return score, persons, vehicles

# =========================================================
# SAVE DECISION
# =========================================================

def save_decision(decisions, path, priority):

    if path not in decisions:

        decisions[path] = priority

    else:

        decisions[path] = max(
            decisions[path],
            priority
        )

# =========================================================
# SESSION JSON
# =========================================================

def timestamp():

    return datetime.now().replace(microsecond=0).isoformat()


def create_session():

    return {
        "status": "running",
        "started_at": timestamp(),
        "finished_at": None,
        "clips_processed": 0,
        "important": 0,
        "review": 0,
        "ignore": 0,
        "incidents": []
    }


def add_incident(
    session,
    path,
    event_id,
    label,
    ai,
    event_score,
    persons,
    vehicles,
    active_frames,
    frame_path
):

    incident_number = len(session["incidents"]) + 1

    session["incidents"].append(
        {
            "id": f"incident_{incident_number:04d}",
            "source_video": os.path.basename(path),
            "event_id": event_id,
            "severity": label,
            "ai_decision": ai,
            "score": round(event_score, 1),
            "persons": persons,
            "vehicles": vehicles,
            "active_frames": active_frames,
            "thumbnail": frame_path,
            "created_at": timestamp()
        }
    )


def write_session_json(session):

    with open(LATEST_SESSION_JSON, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2)

# =========================================================
# FINALIZE EVENT
# =========================================================

def finalize_event(
    path,
    decisions,
    session,
    event_id,
    best_frame,
    event_score,
    persons,
    vehicles,
    active_frames
):

    if best_frame is None:
        return

    frame_name = (
        f"{os.path.basename(path)}"
        f"_event_{event_id}.jpg"
    )

    frame_path = os.path.join(
        FRAMES,
        frame_name
    )

    cv2.imwrite(frame_path, best_frame)

    # =====================================================
    # AI FINAL DECISION
    # =====================================================

    ai = run_ai(frame_path)

    if ai == "IMPORTANT":

        priority = 2

    elif ai == "REVIEW":

        priority = 1

    else:

        priority = 0

    # =====================================================
    # LABEL/COLOR
    # =====================================================

    if priority == 2:

        label = "IMPORTANT"
        color = "red"

    elif priority == 1:

        label = "REVIEW"
        color = "yellow"

    else:

        label = "IGNORE"
        color = "green"

    # =====================================================
    # LOGGING
    # =====================================================

    console.print(
        f"  [{color}]EVENT {event_id}[/{color}] "
        f"| score={event_score:.1f} "
        f"| frames={active_frames} "
        f"| persons={persons} "
        f"| vehicles={vehicles} "
        f"| AI={ai} "
        f"| FINAL={label}"
    )

    add_incident(
        session,
        path,
        event_id,
        label,
        ai,
        event_score,
        persons,
        vehicles,
        active_frames,
        frame_path
    )

    save_decision(
        decisions,
        path,
        priority
    )

# =========================================================
# PROCESS VIDEO
# =========================================================

def process_video(path, decisions, session):

    cap = cv2.VideoCapture(path)

    if not cap.isOpened():

        console.print(
            f"[red]FAILED TO OPEN:[/red] {path}"
        )

        save_decision(
            decisions,
            path,
            0
        )

        return

    fps = cap.get(cv2.CAP_PROP_FPS) or 30

    step = max(
        1,
        int(fps / SAMPLE_FPS)
    )

    frame_i = 0

    rolling = 0.0
    active = False

    event_score = 0
    active_frames = 0

    persons = 0
    vehicles = 0

    best_frame = None
    best_score = 0

    last_activity = 0

    event_id = 0

    console.print(
        f"\n[bold blue]Scanning:[/bold blue] "
        f"{os.path.basename(path)}"
    )

    while True:

        ret, frame = cap.read()

        if not ret:
            break

        if frame_i % step != 0:

            frame_i += 1
            continue

        score, p, v = analyze(frame)

        rolling = rolling * 0.85 + score

        now = time.time()

        # =================================================
        # START EVENT
        # =================================================

        if not active and rolling > EVENT_TRIGGER:

            active = True

            event_score = 0
            active_frames = 0

            persons = 0
            vehicles = 0

            best_frame = None
            best_score = 0

            console.print(
                f"  [cyan]event {event_id} started[/cyan]"
            )

        # =================================================
        # ACTIVE EVENT
        # =================================================

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

            # =============================================
            # END EVENT
            # =============================================

            if (
                last_activity
                and now - last_activity >
                EVENT_END_TIMEOUT
            ):

                finalize_event(
                    path,
                    decisions,
                    session,
                    event_id,
                    best_frame,
                    event_score,
                    persons,
                    vehicles,
                    active_frames
                )

                active = False

                rolling = 0.0

                event_score = 0
                active_frames = 0

                persons = 0
                vehicles = 0

                best_frame = None
                best_score = 0

                last_activity = 0

                event_id += 1

        frame_i += 1

    # =====================================================
    # FINALIZE UNFINISHED EVENT
    # =====================================================

    if active and best_frame is not None:

        console.print(
            "  [yellow]finalizing unfinished event[/yellow]"
        )

        finalize_event(
            path,
            decisions,
            session,
            event_id,
            best_frame,
            event_score,
            persons,
            vehicles,
            active_frames
        )

    # =====================================================
    # NO EVENTS
    # =====================================================

    if path not in decisions:

        console.print(
            "  [green]no events detected[/green]"
        )

        save_decision(
            decisions,
            path,
            0
        )

    cap.release()

# =========================================================
# MOVE FILES
# =========================================================

def move_files(decisions):

    def folder(priority):

        if priority == 2:
            return IMPORTANT

        elif priority == 1:
            return REVIEW

        return IGNORE

    console.print(
        "\n[bold cyan]Moving files...[/bold cyan]"
    )

    for src, priority in decisions.items():

        dst_folder = folder(priority)

        name = os.path.basename(src)

        dst = os.path.join(
            dst_folder,
            name
        )

        base, ext = os.path.splitext(name)

        i = 1

        while os.path.exists(dst):

            dst = os.path.join(
                dst_folder,
                f"{base}_{i}{ext}"
            )

            i += 1

        try:

            shutil.move(src, dst)

            console.print(
                f"  → {name} "
                f"→ "
                f"[bold]{os.path.basename(dst_folder)}[/bold]"
            )

        except Exception as e:

            console.print(
                f"[red]MOVE FAILED:[/red] {src}"
            )

            console.print(e)

# =========================================================
# CLEAN EMPTY FOLDERS
# =========================================================

def clean_empty_dirs(folder):

    console.print(
        "\n[bold cyan]Cleaning folders...[/bold cyan]"
    )

    for root, dirs, files in os.walk(
        folder,
        topdown=False
    ):

        # remove metadata/junk
        for f in files:

            full = os.path.join(root, f)

            if f.lower().endswith(
                (
                    ".json",
                    ".thumb",
                    ".ini"
                )
            ):

                try:
                    os.remove(full)
                except:
                    pass

        remaining = os.listdir(root)

        if remaining:
            continue

        try:

            os.rmdir(root)

            console.print(
                f"  removed: {root}"
            )

        except:
            pass

# =========================================================
# SUMMARY
# =========================================================

def generate_summary(decisions):

    important = 0
    review = 0
    ignore = 0

    for p in decisions.values():

        if p == 2:
            important += 1

        elif p == 1:
            review += 1

        else:
            ignore += 1

    table = Table(title="Scan Results")

    table.add_column("Category")
    table.add_column("Count")

    table.add_row(
        "[red]IMPORTANT[/red]",
        str(important)
    )

    table.add_row(
        "[yellow]REVIEW[/yellow]",
        str(review)
    )

    table.add_row(
        "[green]IGNORE[/green]",
        str(ignore)
    )

    console.print(table)

# =========================================================
# MAIN
# =========================================================

def main():

    session = create_session()

    videos = []

    decisions = {}

    for root, _, files in os.walk(INCOMING):

        for f in files:

            if f.lower().endswith(".mp4"):

                videos.append(
                    os.path.join(root, f)
                )

    total = len(videos)

    if total == 0:

        session["status"] = "complete"
        session["finished_at"] = timestamp()
        session["clips_processed"] = 0

        write_session_json(session)

        console.print(
            "[bold red]No videos found.[/bold red]"
        )

        return

    console.print(
        Panel.fit(
            f"Found {total} Tesla clips",
            title="Tesla AI Scanner"
        )
    )

    with Progress(

        TextColumn(
            "[progress.description]{task.description}"
        ),

        BarColumn(),

        "[progress.percentage]{task.percentage:>3.0f}%",

        TimeElapsedColumn(),

        TimeRemainingColumn(),

        console=console

    ) as progress:

        task = progress.add_task(
            "[cyan]Processing videos...",
            total=total
        )

        for video in videos:

            process_video(
                video,
                decisions,
                session
            )

            progress.advance(task)

    move_files(decisions)

    clean_empty_dirs(INCOMING)

    generate_summary(decisions)

    important = 0
    review = 0
    ignore = 0

    for p in decisions.values():

        if p == 2:
            important += 1

        elif p == 1:
            review += 1

        else:
            ignore += 1

    session["status"] = "complete"
    session["finished_at"] = timestamp()
    session["clips_processed"] = total
    session["important"] = important
    session["review"] = review
    session["ignore"] = ignore

    write_session_json(session)

    console.print(
        "\n[bold green]DONE.[/bold green]"
    )

# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    main()
