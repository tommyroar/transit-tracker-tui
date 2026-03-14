import cv2
import os

IMAGE_PATH = "hardware_averaged.jpg"
OUTPUT_PATH = "hardware_cropped.jpg"

def main():
    if not os.path.exists(IMAGE_PATH):
        print(f"Error: {IMAGE_PATH} not found.")
        return
        
    img = cv2.imread(IMAGE_PATH)
    if img is None:
        print("Error: Could not read image.")
        return
        
    print(f"Original shape: {img.shape}")
    # You might want to adjust these crop coordinates
    # For now, let's just save a copy or do a dummy crop
    # h, w = img.shape[:2]
    # cropped = img[h//4:3*h//4, w//4:3*w//4] 
    
    cv2.imwrite(OUTPUT_PATH, img)
    print(f"Saved {OUTPUT_PATH}")

if __name__ == "__main__":
    main()
