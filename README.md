# ResolShaders — Universal Windows Game Visual Overlay

ResolShaders is a Windows desktop post-processing overlay for a selected visible game or application window. It uses Windows Graphics Capture (WGC) to capture the selected window HWND directly. It never hides/shows itself between frames, so it avoids the old flicker loop and does not capture its own processed output. A bounded latest-frame slot prevents slow UI updates from building a large queue.

**Compatibility:** Windows 10 1903+ / Windows 11, 64-bit. Select a visible game window from the list. Windowed or borderless mode is recommended. Some games using exclusive fullscreen, protected surfaces, anti-capture settings, or unusual presentation paths may not provide frames. Compatibility with every game/driver cannot be guaranteed.

**Important:** This is screen-space post-processing, not an in-game renderer. It does not inject into games, modify game files, bypass anti-cheat, provide ray tracing, or create true HDR output. The HDR-style control is a tone-mapping approximation.

## Run from source
1. Extract this ZIP.
2. Open a terminal in the `ResolShaders` folder.
3. Run `py -3.11 -m pip install -r requirements.txt`
4. Run `py src\main.py`

## Build a standalone EXE with GitHub Actions
1. Upload the contents of this folder (including `.github`) to a GitHub repository.
2. Open **Actions** and run **Build ResolShaders for Windows**.
3. Download the `ResolShaders-Windows-x64` artifact from the completed run.

## Use
1. Launch a game normally.
2. Open ResolShaders and click **Refresh game windows**.
3. Select the game window and click **Start on selected window**.
4. Press F8 to hide/show the overlay or F9 to stop it.

## Performance
Start at 30 FPS and 50% processing resolution, especially on lower-end GPUs. Higher FPS settings are targets only; Windows capture cadence, CPU/GPU performance, resolution, and game presentation affect actual rates.

## Effects
Bloom approximation, HDR-style tone mapping, brightness, contrast, saturation, warmth, sharpening, vignette, and optional chromatic aberration.

## Troubleshooting
- Use windowed or borderless mode.
- If a window gives a black/empty capture, try another display mode or select the game again.
- If the game window closes, stop and restart capture.
- Keep Windows Security enabled; unsigned EXEs may receive reputation warnings.
