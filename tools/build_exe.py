"""Сборка dist/HiddenPath.exe — одна и та же для build_installer.bat и GitHub Actions."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import version_info  # noqa: E402


def main():
    # публичная сборка: никаких ключей и подписок внутри — пользователь вставляет свои
    if (ROOT / "keys.txt").exists():
        sys.exit("keys.txt must not be in the public build folder - remove it and run again")
    version_info.main()
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--noconsole", "--uac-admin",
           "--add-data", "ui.html;.", "--add-data", "icon.png;.", "--add-data", "icon.ico;.",
           "--icon", "icon.ico", "--version-file", str(ROOT / "build" / "version_info.txt"),
           "--hidden-import", "pystray._win32", "--hidden-import", "certifi",
           "--exclude-module", "numpy", "--exclude-module", "tkinter",
           "--name", "HiddenPath", "zapret_manager.py"]
    return subprocess.call(cmd, cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
