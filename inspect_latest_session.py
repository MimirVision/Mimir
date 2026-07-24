import json
import os


BASE = r"C:\Mimir_Backend"
LATEST_SESSION_JSON = os.path.join(BASE, "MimirOutputV2", "latest_session.json")
PATH_FIELDS = [
    "source_video",
    "source_clip",
    "video_preview",
    "thumbnail",
    "contact_sheet",
]


def value_or_missing(value):
    if value is None:
        return "(missing)"

    text = str(value).strip()

    if not text:
        return "(missing)"

    return text


def path_exists(value):
    if not value:
        return False

    try:
        return os.path.exists(str(value))
    except (TypeError, ValueError, OSError):
        return False


def print_field(label, value, indent=""):
    print(f"{indent}{label}: {value_or_missing(value)}")


def print_path_field(label, value, indent=""):
    print_field(label, value, indent)
    print(f"{indent}  exists: {'yes' if path_exists(value) else 'no'}")


def load_session(path):
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, dict):
        return data

    return {}


def print_session_summary(session):
    incidents = session.get("incidents", [])

    if not isinstance(incidents, list):
        incidents = []

    print("Mimir Latest Session")
    print("====================")
    print_field("status", session.get("status"))
    print_field("clips_processed", session.get("clips_processed"))
    print_field("important", session.get("important"))
    print_field("review", session.get("review"))
    print_field("ignore", session.get("ignore"))
    print_field("number_of_incidents", len(incidents))
    print_field("safe_input_mode", session.get("safe_input_mode"))
    print_field("input_folder", session.get("input_folder"))

    print()
    print("Incidents")
    print("=========")

    if not incidents:
        print("No incidents found.")
        return

    for index, incident in enumerate(incidents, start=1):
        if not isinstance(incident, dict):
            print()
            print(f"Incident {index}")
            print("-" * (9 + len(str(index))))
            print("  Invalid incident entry.")
            continue

        print()
        print(f"Incident {index}")
        print("-" * (9 + len(str(index))))
        print_field("id", incident.get("id"), "  ")
        print_field("severity", incident.get("severity"), "  ")
        print_field("source_video", incident.get("source_video"), "  ")
        print_field("source_clip", incident.get("source_clip"), "  ")
        print_field("video_preview", incident.get("video_preview"), "  ")
        print_field("thumbnail", incident.get("thumbnail"), "  ")
        print_field("contact_sheet", incident.get("contact_sheet"), "  ")
        print_field("summary", incident.get("summary"), "  ")
        print_field("event_type", incident.get("event_type"), "  ")

        print("  Path checks:")
        for field in PATH_FIELDS:
            print_path_field(field, incident.get(field), "    ")


def main():
    if not os.path.exists(LATEST_SESSION_JSON):
        print("latest_session.json was not found.")
        print(f"Expected path: {LATEST_SESSION_JSON}")
        return

    try:
        session = load_session(LATEST_SESSION_JSON)
    except json.JSONDecodeError as error:
        print("Could not parse latest_session.json.")
        print(f"Error: {error}")
        return
    except OSError as error:
        print("Could not read latest_session.json.")
        print(f"Error: {error}")
        return

    print_session_summary(session)


if __name__ == "__main__":
    main()
