import cv2
import os

video_folder = r"C:\TeslaAI\Raw"
output_folder = r"C:\TeslaAI\Frames"

os.makedirs(output_folder, exist_ok=True)

if not os.path.isdir(video_folder):
    raise FileNotFoundError(f"Video folder does not exist: {video_folder}")

for video in os.listdir(video_folder):
    if video.lower().endswith(".mp4"):
        path = os.path.join(video_folder, video)

        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            print(f"Could not open: {path}")
            continue

        fps = cap.get(cv2.CAP_PROP_FPS)
        interval = max(1, int(fps * 2))

        count = 0
        saved = 0

        while cap.isOpened():
            ret, frame = cap.read()

            if not ret:
                break

            if count % interval == 0:
                filename = f"{video}_{saved}.jpg"
                cv2.imwrite(os.path.join(output_folder, filename), frame)
                saved += 1

            count += 1

        cap.release()

print("Done.")
