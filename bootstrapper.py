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

    if vendor_dir not in sys.path:
        sys.path.insert(0, vendor_dir)

    required_packages = [
        "pyttsx3",
        "vosk",
        "rapidfuzz",
        "scipy",
        "numpy",
        "eng-to-ipa",
    ]

    missing_packages = []

    for package in required_packages:
        try:
            __import__(package.replace("-", "_"))
        except ImportError:
            missing_packages.append(package)

    if not missing_packages:
        return True

    os.makedirs(vendor_dir, exist_ok=True)

    progress = QProgressDialog(
        "Installing dependencies...", None, 0, len(missing_packages), mw
    )
    progress.setWindowTitle("AnkiPA Setup")
    progress.setModal(True)
    progress.setCancelButton(None)
    progress.show()

    total = len(missing_packages)

    for idx, package in enumerate(missing_packages, 1):
        progress.setLabelText(f"Installing {package}...\n({idx}/{total})")
        progress.setValue(idx - 1)
        QApplication.processEvents()

        try:
            print(f"[AnkiPA] Installing {package}...")
            subprocess.check_call(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--target",
                    vendor_dir,
                    "--upgrade",
                    package,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError as e:
            progress.close()
            msg = (
                f"Failed to install dependency: {package}.\n\n"
                f"{str(e)}\n\nCheck internet connection."
            )
            showText(msg, title="AnkiPA Installation Error")
            return False

    progress.setValue(total)
    progress.close()

    importlib.invalidate_caches()

    showInfo("AnkiPA setup complete!", title="AnkiPA Ready")
    return True