# QBasic IDE - desktop, Windows installer, Android

| Folder / file | What it is |
|---|---|
| `qbasic_ide.py` | Desktop app (Windows / macOS / Linux). Code + Output + live Flowchart/DFD, **Export JPG**. |
| `windows/` | `build_windows.bat` + `QBasicIDE.iss` -> `QBasicIDE.exe` and **`QBasicIDE_Setup.exe`** installer |
| `android/` | Kivy mobile app (`main.py`, `qb_engine.py`, `buildozer.spec`) -> **APK** |
| `.github/workflows/build.yml` | Builds the Windows installer AND the Android APK in the cloud |
| `examples/` | Sample .bas programs (installed with the Windows setup) |

## Run the desktop app
    pip install pillow        # only needed for JPG/PNG export
    python qbasic_ide.py

## 1) Windows installer
**Local (on a Windows PC):** install Python 3.9+ (python.org, keep "tcl/tk" ticked) and
[Inno Setup 6](https://jrsoftware.org/isdl.php), then double-click `windows\build_windows.bat`.
Result: `dist\QBasicIDE.exe` (portable) and `installer\QBasicIDE_Setup.exe` (installer with
Start-menu entry, optional desktop icon, optional `.bas` file association, uninstaller).

## 2) Android APK
Buildozer only runs on Linux/macOS (or WSL2 on Windows). Easiest = cloud build, no setup:
1. Create a free GitHub repository and upload this whole folder (keep `.github`).
2. Open the **Actions** tab -> *Build installers* -> *Run workflow* (it also runs on every push).
3. When finished, download **QBasicIDE-Android-APK** (the .apk) and **QBasicIDE-Windows**
   (the installer) from the run's *Artifacts* section. First APK build takes ~20-30 min.
4. Copy the APK to the phone, allow "install unknown apps", tap to install.

Local alternative (Ubuntu/WSL2): `cd android && pip install buildozer cython && buildozer android debug`
-> APK appears in `android/bin/`.

## Mobile app notes
* Tabs: CODE / OUTPUT / CHART. RUN switches to OUTPUT; type INPUT answers in the bar at the bottom.
* CHART tab: Flowchart, DFD 0, DFD 1; pinch to zoom, drag to pan, **Export** saves a JPG
  (to Downloads if allowed, otherwise the app's private folder - the path is shown).
* Live trace works like the desktop app (box lights up while running; delay / Pause / Step in the top bar).
* Not in the mobile version: SCREEN graphics (PSET/LINE/CIRCLE are accepted but not drawn),
  keyboard keys for INKEY$, and COLOR background shading in the output.
