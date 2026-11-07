"""Сведения о программе для HiddenPath.exe (Свойства файла → Подробно).

Версия берётся из APP_VERSION в zapret_manager.py. Название и версия одинаковы
в exe и установщике — этого требует подпись SignPath (metadata restrictions).
Запуск: python tools/version_info.py  →  build/version_info.txt, его берёт PyInstaller (--version-file).
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRODUCT = "Hidden Path"
COMPANY = "NapsterFO"
DESCRIPTION = "Hidden Path: zapret, VLESS (Xray) and Telegram proxy for Windows"
COPYRIGHT = "Copyright (c) 2026 NapsterFO. MIT License."


def app_version():
    text = (ROOT / "zapret_manager.py").read_text(encoding="utf-8")
    return re.search(r'^APP_VERSION = "(\d+\.\d+\.\d+)"', text, re.M).group(1)


def main():
    ver = app_version()
    nums = tuple(int(x) for x in ver.split(".")) + (0,)
    strings = {
        "CompanyName": COMPANY, "FileDescription": DESCRIPTION, "FileVersion": ver,
        "InternalName": "HiddenPath", "LegalCopyright": COPYRIGHT,
        "OriginalFilename": "HiddenPath.exe", "ProductName": PRODUCT, "ProductVersion": ver,
    }
    table = ",\n          ".join(f"StringStruct({k!r}, {v!r})" for k, v in strings.items())
    out = ROOT / "build" / "version_info.txt"
    out.parent.mkdir(exist_ok=True)
    out.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
          {table}])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")
    print(f"{out} ({ver})")


if __name__ == "__main__":
    sys.exit(main())
