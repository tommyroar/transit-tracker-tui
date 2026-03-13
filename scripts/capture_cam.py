import cv2
import time
import sys

def main():
    # Try indices 0 to 4
    for i in range(5):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            # Let it warm up and focus
            time.sleep(1.5)
            ret, frame = cap.read()
            if ret:
                cv2.imwrite("cam.jpg", frame)
                print(f"SUCCESS: Captured from camera {i}")
                cap.release()
                sys.exit(0)
            cap.release()
    print("ERROR: Could not capture from any camera.")
    sys.exit(1)

if __name__ == "__main__":
    main()
