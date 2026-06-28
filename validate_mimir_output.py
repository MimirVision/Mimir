import json
import os
import sys

SESSION_PATH = r"C:\Mimir_Backend\MimirOutput\latest_session.json"


def fail(message):
    print(f"FAILED: {message}")
    sys.exit(1)


def main():
    if not os.path.exists(SESSION_PATH):
        fail(f"Session file does not exist: {SESSION_PATH}")

    try:
        with open(SESSION_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        fail(f"Could not read JSON: {e}")

    required_top_fields = [
        "status",
        "started_at",
        "finished_at",
        "clips_processed",
        "important",
        "review",
        "ignore",
        "incidents",
    ]

    for field in required_top_fields:
        if field not in data:
            fail(f"Missing top-level field: {field}")

    if not isinstance(data["incidents"], list):
        fail("incidents must be a list")

    for i, incident in enumerate(data["incidents"], start=1):
        required_incident_fields = [
            "id",
            "source_video",
            "event_id",
            "severity",
            "ai_decision",
            "score",
            "persons",
            "vehicles",
            "active_frames",
            "thumbnail",
            "created_at",
        ]

        for field in required_incident_fields:
            if field not in incident:
                fail(f"Incident {i} missing field: {field}")

        thumbnail = incident.get("thumbnail")
        if thumbnail and not os.path.exists(thumbnail):
            print(f"WARNING: thumbnail does not exist for {incident['id']}: {thumbnail}")

    print("Mimir output looks valid.")
    print(f"Status: {data['status']}")
    print(f"Clips processed: {data['clips_processed']}")
    print(f"Important: {data['important']}")
    print(f"Review: {data['review']}")
    print(f"Ignore: {data['ignore']}")
    print(f"Incidents: {len(data['incidents'])}")


if __name__ == "__main__":
    main()