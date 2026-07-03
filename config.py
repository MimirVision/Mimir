import os

# =========================================================
# BASE PATH
# =========================================================

BASE = os.environ.get("MIMIR_BACKEND_ROOT", r"C:\Mimir_Backend")

# =========================================================
# MAIN FOLDERS
# =========================================================

INCOMING = os.path.join(BASE, "Incoming")
IMPORTANT = os.path.join(BASE, "Important")
REVIEW = os.path.join(BASE, "Review")
IGNORE = os.path.join(BASE, "Ignore")
FRAMES = os.path.join(BASE, "Frames")

# =========================================================
# MIMIR OUTPUT
# =========================================================

MIMIR_OUTPUT = os.path.join(BASE, "MimirOutput")
INCIDENTS_OUTPUT = os.path.join(MIMIR_OUTPUT, "incidents")
LATEST_SESSION_JSON = os.path.join(MIMIR_OUTPUT, "latest_session.json")

# =========================================================
# MODELS
# =========================================================

LLM_MODEL = "llava:7b"

# Change this to test different YOLO models:
# "yolov8n.pt"
# "yolo11n.pt"
# "yolo11s.pt"
YOLO_MODEL = "yolo11n.pt"

# =========================================================
# YOLO CLASSES
# =========================================================

PERSON = 0

# COCO:
# 2 = car
# 3 = motorcycle
# 5 = bus
# 7 = truck
VEHICLES = {2, 3, 5, 7}

# =========================================================
# DETECTION TUNING
# =========================================================

MIN_CONF = 0.40
MIN_AREA_RATIO = 0.012

# =========================================================
# SAMPLING
# =========================================================

SAMPLE_FPS = 2.0

# =========================================================
# EVENT LOGIC
# =========================================================

EVENT_TRIGGER = 14.0
EVENT_END_TIMEOUT = 2.0
MIN_EVENT_FRAMES = 4

# =========================================================
# AI
# =========================================================

AI_ENABLED = True

# =========================================================
# FRAME CROPPING
# =========================================================

IGNORE_TOP_RATIO = 0.20
