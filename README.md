# RBloxTrex — Windows Roblox Shader Launcher

RBloxTrex is a desktop launcher plus screen-space shader overlay for Windows. It has a custom app icon, can open Roblox, detects the Roblox Player window, and starts the overlay on that selected window.

**Safety / technical note:** This does not inject into Roblox, modify game files, hook DirectX, or bypass anti-cheat. It captures the visible monitor region and places a filtered overlay over the selected Roblox window. It is intended for windowed or borderless-windowed Roblox. It cannot guarantee capture in exclusive fullscreen or protected content.

## Requirements
- Windows 10/11, 64-bit
- Python 3.10–3.14 (64-bit)

## Run from source
1. Extract this ZIP.
2. Open Command Prompt in the extracted `RBloxTrex` folder.
3. Run:
   ```cmd
   py -m pip install -r requirements.txt
   py src\main.py
   ```

## Get a standalone EXE (recommended)
You do not need to build it on your own PC if you use GitHub Actions:

1. Create a GitHub repository and upload the contents of this folder (including the hidden `.github` folder).
2. Open the repository's **Actions** tab.
3. Select **Build RBloxTrex for Windows** and click **Run workflow**.
4. When it finishes, open the run and download **RBloxTrex-Windows-x64** under Artifacts.
5. Extract it and run `RBloxTrex.exe`.

The workflow builds on a Windows runner and packages a single 64-bit EXE. The target PC does not need Python installed.

To build locally instead, use Windows with Python 3.11 (64-bit), then double-click `build_exe.bat`. The output is `dist\\RBloxTrex.exe`. A version tag such as `v1.0.0` also triggers a GitHub Release containing the EXE.

## How to use
1. Open RBloxTrex.
2. Click **Launch Roblox + Shader**. The app opens Roblox through its registered `roblox://` handler (or searches the installed Roblox Player executable).
3. It waits for the Roblox Player window, selects it, and starts the overlay.
4. Alternatively, launch Roblox yourself, click **Refresh windows**, select Roblox Player, and click **Start on selected window**.
5. Use **Stop overlay** or F9 to stop. F8 hides/shows the overlay.

## GT 1030
Use the **Low-end (GT 1030)** preset, 15 FPS and half-resolution processing. If performance is still poor, close other GPU-heavy applications.

## Included effects
Bloom approximation, HDR-style tone mapping (not true HDR output), saturation, warmth, sharpening, vignette and optional chromatic aberration. This is not ray tracing, true ambient occlusion, physically based shadows or real depth-of-field.

## Troubleshooting
- If Roblox does not launch, install Roblox from https://www.roblox.com/ and sign in once.
- Use windowed or borderless mode.
- If Roblox is not detected, click Refresh windows after it has opened.
- If the overlay appears black, protected content or capture restrictions may be blocking screen capture.
- If you see a repeated/recursive image, press F9 and restart the app.
