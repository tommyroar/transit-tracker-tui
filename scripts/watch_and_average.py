import os
import time
import cv2
import numpy as np
import subprocess

MEDIA_DIR = "/Volumes/Untitled/DCIM/DJI_001"
PREVIOUS_FILES = {"DJI_20260314111737_0009_D.MP4"}

def get_newest_mp4():
    if not os.path.exists(MEDIA_DIR):
        return None
    files = [f for f in os.listdir(MEDIA_DIR) if f.upper().endswith('.MP4')]
    if not files:
        return None
    files.sort(key=lambda x: os.path.getmtime(os.path.join(MEDIA_DIR, x)), reverse=True)
    return files[0]

def main():
    print("Waiting for DJI Action 4 to reconnect...")
    new_video = None
    
    # Poll for up to 5 minutes (150 * 2s)
    for _ in range(150):
        newest = get_newest_mp4()
        if newest and newest not in PREVIOUS_FILES:
            new_video = os.path.join(MEDIA_DIR, newest)
            print(f"Detected new video: {new_video}")
            time.sleep(4) # Brief pause to ensure the OS has finished mounting/indexing the file
            break
        time.sleep(2)
        
    if not new_video:
        print("Timed out waiting for new video. Run the script again when ready.")
        return
        
    print(f"Processing {new_video}...")
    cap = cv2.VideoCapture(new_video)
    frames = []
    count = 0
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        frames.append(frame.astype(np.float32))
        count += 1
        if count % 10 == 0:
            print(f"Read {count} frames...")
            
    cap.release()
    
    if frames:
        avg_frame = np.mean(frames, axis=0).astype(np.uint8)
        output_path = "hardware_averaged.jpg"
        cv2.imwrite(output_path, avg_frame)
        print(f"Saved averaged image to {output_path}")
        subprocess.run(["open", output_path])
    else:
        print("No frames could be extracted.")

if __name__ == "__main__":
    main()
