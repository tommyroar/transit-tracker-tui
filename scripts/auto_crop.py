import cv2
import numpy as np
import os

INPUT_IMG = "hardware_averaged.jpg"
SIM_IMG = "sim_rendered.png"
OUTPUT_IMG = "hardware_cropped.jpg"

def get_pink_mask(img):
    """Isolates the neon/hot pink pixels common in Route 14."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # Hot Pink/Magenta range
    lower_pink = np.array([140, 50, 50])
    upper_pink = np.array([175, 255, 255])
    mask = cv2.inRange(hsv, lower_pink, upper_pink)
    return mask

def match_color_template(image_path, sim_path, output_path):
    if not os.path.exists(image_path) or not os.path.exists(sim_path):
        print("Missing input or simulator image.")
        return

    img = cv2.imread(image_path)
    sim = cv2.imread(sim_path)
    img_h, img_w = img.shape[:2]
    img_center = (img_w // 2, img_h // 2)

    # 1. Get Pink Masks for both (targeting the "14")
    print("Creating color masks for Route 14 (Pink)...")
    img_mask = get_pink_mask(img)
    sim_mask = get_pink_mask(sim)
    
    # Check if we even found pink in the photo
    pink_count = np.sum(img_mask > 0)
    if pink_count < 100:
        print(f"Warning: Only {pink_count} pink pixels found. Display might be off or color-shifted.")
        # Fallback to yellow if pink fails? 
        # For now let's stick to pink as requested.

    found = None
    
    # 2. Multi-scale Template Matching on the MASKS
    print(f"Scanning for Pink '14' pattern near center...")
    
    for scale in np.linspace(0.4, 6.0, 60):
        resized_sim_mask = cv2.resize(sim_mask, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        (tH, tW) = resized_sim_mask.shape[:2]
        
        if tH > img_h or tW > img_w:
            continue
            
        res = cv2.matchTemplate(img_mask, resized_sim_mask, cv2.TM_CCOEFF_NORMED)
        
        # Center-weighting (0.7 penalty for being at the very edge)
        res_h, res_w = res.shape
        y_indices, x_indices = np.indices((res_h, res_w))
        match_centers_x = x_indices + (tW // 2)
        match_centers_y = y_indices + (tH // 2)
        dist_from_center = np.sqrt(((match_centers_x - img_center[0]) / img_w)**2 + 
                                   ((match_centers_y - img_center[1]) / img_h)**2)
        res_weighted = res * (1.0 - 0.7 * dist_from_center) 
        
        (_, maxVal, _, maxLoc) = cv2.minMaxLoc(res_weighted)
        
        if found is None or maxVal > found[0]:
            found = (maxVal, maxLoc, scale, tW, tH)
            if maxVal > 0.4: # Early reporting of strong matches
                print(f"  Scale {scale:.2f}: Correlation {maxVal:.4f}")

    if found and found[0] > 0.01:
        (maxVal, maxLoc, scale, tW, tH) = found
        x, y = maxLoc
        
        # Add 30% padding to see the surrounding board
        pad_x = int(tW * 0.3)
        pad_y = int(tH * 0.3)
        
        x_start = max(0, x - pad_x)
        y_start = max(0, y - pad_y)
        x_end = min(img_w, x + tW + pad_x)
        y_end = min(img_h, y + tH + pad_y)
        
        cropped = img[y_start:y_end, x_start:x_end]
        cv2.imwrite(output_path, cropped)
        print(f"SUCCESS: Found Route 14 at {maxLoc} (scale {scale:.2f}). Saved to {output_path}")
        
        # Debugging
        cv2.rectangle(img, (x, y), (x + tW, y + tH), (0, 255, 0), 10)
        cv2.imwrite("debug_pink_mask.jpg", img_mask)
        cv2.imwrite("debug_template_match.jpg", img)
    else:
        print("Template matching failed to find the Pink pattern.")

if __name__ == "__main__":
    match_color_template(INPUT_IMG, SIM_IMG, OUTPUT_IMG)
