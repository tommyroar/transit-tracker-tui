import os
import subprocess
import sys


def verify_launch():
    print("🚀 Verifying CLI launch...")
    
    # Set PYTHONPATH to include src
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    # No special env vars needed — test isolation handled by pytest markers
    
    # Start the CLI in a way that we can kill it
    # We use --help as a simple smoke test that doesn't require interaction
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "transit_tracker.cli", "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True
        )
        
        stdout, stderr = process.communicate(timeout=10)
        
        if process.returncode == 0:
            print("✅ CLI launch verification successful!")
            return True
        else:
            print(f"❌ CLI launch failed with exit code {process.returncode}")
            print(f"Error output:\n{stderr}")
            return False
            
    except Exception as e:
        print(f"❌ CLI launch verification failed with error: {e}")
        return False

if __name__ == "__main__":
    if not verify_launch():
        sys.exit(1)
