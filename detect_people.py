from ultralytics import YOLO
import os

model = YOLO("yolov8n.pt")

folder = r"C:\TeslaAI\Frames"
image_extensions = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
min_confidence = 0.35

if not os.path.isdir(folder):
    raise FileNotFoundError(f"Frame folder does not exist: {folder}")

for image in os.listdir(folder):
    if not image.lower().endswith(image_extensions):
        continue

    path = os.path.join(folder, image)
    if not os.path.isfile(path):
        continue

    results = model(path, verbose=False)

    person_detected = False
    best_confidence = 0
    for r in results:
        for box in r.boxes:
            cls = int(box.cls[0])
            confidence = float(box.conf[0])

            if cls == 0 and confidence >= min_confidence:
                person_detected = True
                best_confidence = max(best_confidence, confidence)
                break

        if person_detected:
            break

    if person_detected:
        print(f"PERSON DETECTED: {image} confidence={best_confidence:.2f}")
