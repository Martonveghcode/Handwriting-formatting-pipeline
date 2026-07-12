# MTYH

Windows desktop app that combines the MyText handwriting tools into one PySide6 application.

MTYH 2 adds a responsive formatter workspace, spreadsheet-style character map,
page-by-page Quick Copy queue, persistent themes/window settings, a default Stitch
Export workflow, crash logging, and the continuous line-generation stroke from the
original `generated image to printable V2.py` tool.

The older standalone scripts remain in this repository for reference; the `mtyh`
package is the maintained desktop application.

Appearance settings include colour-family palette browsing. Stitch Export remembers
a configurable image import folder and can optionally skip its delete-originals
confirmation. Quick Copy can also be assigned an optional global hotkey.

## Run

```powershell
py -3.11 -m pip install -r requirements.txt
py -3.11 -m mtyh
```

## Test

```powershell
py -3.11 -m pytest
```

## Build EXE

```powershell
py -3.11 -m PyInstaller --noconfirm --clean --windowed --onefile --name MTYH --icon "mtyh\resources\text formater.ico" --add-data "mtyh\resources\text formater.ico;mtyh\resources" mtyh\__main__.py
```

The executable is created at `dist\MTYH.exe`.

## User data

Settings and logs are stored per Windows user in `%APPDATA%\MTYH`. The executable
directory remains read-only. Invalid or missing settings safely fall back to defaults.
