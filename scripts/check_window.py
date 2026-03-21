
import Quartz


def check_window_visibility(app_name):
    # Get all windows on the screen
    window_list = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID)
    
    found = False
    for window in window_list:
        owner_name = window.get(Quartz.kCGWindowOwnerName, 'Unknown')
        if app_name.lower() in owner_name.lower():
            window_id = window.get(Quartz.kCGWindowNumber)
            is_visible = window.get(Quartz.kCGWindowIsOnscreen, False)
            print(f"FOUND: '{owner_name}' (ID: {window_id}), Onscreen: {is_visible}")
            found = True
            
    if not found:
        print(f"No windows found for application: '{app_name}'")
        return False
    return True

if __name__ == "__main__":
    check_window_visibility("transit-tracker")
