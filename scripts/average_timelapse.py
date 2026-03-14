import cv2
import os
import numpy as np

VIDEO_PATH = "/Volumes/Untitled/DCIM/DJI_001/DJI_20260314111737_0009_D.MP4"
OUTPUT_DIR = "captures"

def extract_and_average():
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
        
    cap = cv2.VideoCapture(VIDEO_PATH)
    if not cap.isOpened():
        print("Error: Could not open video.")
        return

    frames = []
    count = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frames.append(frame.astype(np.float32))
        count += 1
        if count % 10 == 0:
            print(f"Read {count} frames...")

    if not frames:
        print("No frames found.")
        return

    # Average the frames
    avg_frame = np.mean(frames, axis=0).astype(np.uint8)
    
    output_path = "hardware_averaged.jpg"
    cv2.imwrite(output_path, avg_frame)
    print(f"Success! Averaged {len(frames)} frames and saved to {output_path}")
    
    cap.release()

if __name__ == "__main__":
    extract_and_average()
