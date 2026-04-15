import os
import sys
import subprocess
import importlib

from aqt.utils import showText, showInfo
from aqt.qt import QProgressDialog, QApplication
from aqt import mw


def ensure_dependencies():
    addon_dir = os.path.dirname(__file__)
    vendor_dir = os.path.join(addon_dir, "vendor")

    # Add vendor directory to path
    if vendor_dir not in sys.path:
        sys.path.insert(0, vendor_dir)

    required_packages = ["pyttsx3", "vosk", "g2p_en", "rapidfuzz", "scipy"] 
    missing_packages = []
    
    # Check if packages are loadable
    for package in required_packages:
        try:
            __import__(package)
        except ImportError:
            missing_packages.append(package)
    
    # If everything is installed then exit
    if not missing_packages:
        return 

    os.makedirs(vendor_dir, exist_ok=True)
    
    # Create progress dialog
    progress = QProgressDialog("Installing dependencies...", None, 0, len(missing_packages), mw)
    progress.setWindowTitle("AnkiPA Setup")
    progress.setModal(True)
    progress.setCancelButton(None)  # Don't allow cancel
    progress.show()
    
    # Use Anki's bundled Python executable to run pip
    total = len(missing_packages)
    for idx, package in enumerate(missing_packages, 1):
        progress.setLabelText(f"Installing {package}...\n({idx}/{total})")
        progress.setValue(idx - 1)
        QApplication.processEvents()  # Update UI
        
        try:
            print(f"[AnkiPA] ({idx}/{total}) Installing {package}...")
            subprocess.check_call([
                sys.executable, "-m", "pip", "install", 
                "--target", vendor_dir, 
                "--upgrade",
                package
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"[AnkiPA] ({idx}/{total}) Successfully installed {package}")
        except subprocess.CalledProcessError as e:
            progress.close()
            msg = f"Failed to install dependency: {package}.\n\nError details: {str(e)}\n\nPlease ensure you are connected to the internet."
            print(f"[AnkiPA] ERROR: {msg}")
            showText(msg, title="AnkiPA Installation Error")
            return

    progress.setValue(total)
    progress.close()
    
    importlib.invalidate_caches()
    print("[AnkiPA] All dependencies installed successfully!")
    showInfo("AnkiPA setup complete! The addon is ready to use.", title="AnkiPA Ready")
