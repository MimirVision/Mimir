import json
import os
from datetime import datetime
from pathlib import Path

from ultralytics import YOLO

from config import BASE, MIMIR_OUTPUT, PERSON, VEHICLES, YOLO_MODEL


TEST_SET = os.path.join(BASE, "TestSet")
TRACKING_OUTPUT = os.path.join(MIMIR_OUTPUT, "tracking_test.json")
VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4"}
TRACKED_CLASSES = {PERSON, *VEHICLES}
COCO_CLASS_NAMES = {
    0: "person",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}


def iso_now():
    return datetime.now().replace(microsecond=0).isoformat()


def resolve_model_path():
    if os.path.isabs(YOLO_MODEL):
        return YOLO_MODEL

    local_model = os.path.join(BASE, YOLO_MODEL)
    if os.path.exists(local_model):
        return local_model

    return YOLO_MODEL


def find_videos():
    test_set = Path(TEST_SET)

    if not test_set.exists():
        test_set.mkdir(parents=True, exist_ok=True)
        print(f"Created test video folder: {TEST_SET}")
        print("Add videos there, then run this script again.")
        return []

    return sorted(
        path
        for path in test_set.iterdir()
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )


def class_name_for(model, class_id):
    names = getattr(model, "names", {})
    return names.get(class_id, COCO_CLASS_NAMES.get(class_id, str(class_id)))


def update_track(track, frame_index, confidence):
    track["first_frame_seen"] = min(track["first_frame_seen"], frame_index)
    track["last_frame_seen"] = max(track["last_frame_seen"], frame_index)
    track["frames_seen"] += 1
    track["max_confidence"] = max(track["max_confidence"], confidence)


def track_video(model, video_path):
    tracks = {}

    results = model.track(
        source=str(video_path),
        classes=sorted(TRACKED_CLASSES),
        persist=True,
        stream=True,
        verbose=False,
    )

    for frame_index, result in enumerate(results):
        boxes = getattr(result, "boxes", None)
        if boxes is None or boxes.id is None:
            continue

        track_ids = boxes.id.cpu().tolist()
        class_ids = boxes.cls.cpu().tolist()
        confidences = boxes.conf.cpu().tolist()

        for raw_track_id, raw_class_id, raw_confidence in zip(
            track_ids,
            class_ids,
            confidences,
        ):
            track_id = int(raw_track_id)
            class_id = int(raw_class_id)
            confidence = float(raw_confidence)
            key = (track_id, class_id)

            if key not in tracks:
                tracks[key] = {
                    "video_name": video_path.name,
                    "track_id": track_id,
                    "class_id": class_id,
                    "class_name": class_name_for(model, class_id),
                    "first_frame_seen": frame_index,
                    "last_frame_seen": frame_index,
                    "frames_seen": 0,
                    "max_confidence": 0.0,
                }

            update_track(tracks[key], frame_index, confidence)

    video_tracks = list(tracks.values())
    for track in video_tracks:
        track["max_confidence"] = round(track["max_confidence"], 4)

    return video_tracks


def output_class_name(class_id):
    return COCO_CLASS_NAMES.get(class_id, str(class_id))


def write_output(videos, tracks):
    os.makedirs(MIMIR_OUTPUT, exist_ok=True)

    output = {
        "status": "complete",
        "generated_at": iso_now(),
        "input_folder": TEST_SET,
        "videos_found": len(videos),
        "videos_processed": len(videos),
        "tracked_classes": [
            {
                "class_id": class_id,
                "class_name": output_class_name(class_id),
            }
            for class_id in sorted(TRACKED_CLASSES)
        ],
        "tracked_objects_found": len(tracks),
        "tracks": tracks,
    }

    with open(TRACKING_OUTPUT, "w", encoding="utf-8") as file:
        json.dump(output, file, indent=2)


def print_summary(videos_found, videos_processed, tracks_found):
    print("")
    print("Tracking test complete.")
    print(f"Videos found: {videos_found}")
    print(f"Videos processed: {videos_processed}")
    print(f"Tracked objects found: {tracks_found}")
    print(f"Output path: {TRACKING_OUTPUT}")


if __name__ == "__main__":
    videos = find_videos()
    print(f"Videos found: {len(videos)}")
    all_tracks = []

    if not videos:
        print(f"No videos to process in: {TEST_SET}")
    else:
        model = YOLO(resolve_model_path())

        for index, video in enumerate(videos, start=1):
            print(f"Processing video {index}/{len(videos)}: {video.name}")
            video_tracks = track_video(model, video)
            all_tracks.extend(video_tracks)
            print(f"Tracked objects in {video.name}: {len(video_tracks)}")

    write_output(videos, all_tracks)
    print_summary(len(videos), len(videos), len(all_tracks))
