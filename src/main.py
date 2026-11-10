import sys
import os
import ctypes
import json
import time
import threading
import wave
import math
import struct
from pathlib import Path

import cv2
import mss
import numpy as np
import win32gui
import win32process
import win32con

from PySide6.QtCore import Qt, QTimer, Signal, QObject, QPropertyAnimation, QEasingCurve, QUrl
from PySide6.QtGui import QImage, QPixmap, QKeySequence, QShortcut, QIcon
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QComboBox, QSlider,
    QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QMessageBox,
    QCheckBox, QGraphicsOpacityEffect
)
from PySide6.QtMultimedia import QSoundEffect

APP_DIR = Path.home() / "AppData" / "Local" / "RBloxTrex"
APP_DIR.mkdir(parents=True, exist_ok=True)
CONFIG = APP_DIR / "settings.json"
user32 = ctypes.windll.user32


def list_windows():
    found = []
    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return
        title = win32gui.GetWindowText(hwnd).strip()
        if not title:
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            import psutil
            proc = psutil.Process(pid).name()
        except Exception:
            proc = "Application"
        # Skip tiny utility windows and this app's own windows.
        try:
            l, t, r, b = win32gui.GetWindowRect(hwnd)
            if r-l < 200 or b-t < 150:
                return
        except Exception:
            return
        if "RBloxTrex" in title:
            return
        found.append((hwnd, f"{title}  —  {proc}"))
    win32gui.EnumWindows(callback, None)
    return found


class EffectEngine:
    def __init__(self):
        self.settings = {
            "brightness": 2, "contrast": 116, "saturation": 108,
            "warmth": 7, "sharpness": 12, "bloom": 22,
            "exposure": 8, "vignette": 8, "chromatic": 0,
            "fps": 15, "scale": 50, "preset": "Low-end (GT 1030)"
        }

    def apply(self, frame):
        s = self.settings
        h, w = frame.shape[:2]
        scale = max(35, min(100, int(s["scale"]))) / 100.0
        if scale < 0.99:
            frame = cv2.resize(frame, (max(2, int(w*scale)), max(2, int(h*scale))),
                               interpolation=cv2.INTER_AREA)

        img = frame.astype(np.float32)
        img = (img - 127.5) * (s["contrast"] / 100.0) + 127.5 + s["brightness"]
        img = np.clip(img, 0, 255).astype(np.uint8)

        # Bloom: blur only bright regions, then add them back softly.
        bloom_strength = max(0, min(100, int(s["bloom"]))) / 100.0
        if bloom_strength > 0.001:
            small = cv2.resize(img, (max(2, img.shape[1]//4), max(2, img.shape[0]//4)),
                                interpolation=cv2.INTER_AREA)
            bright = np.maximum(small.astype(np.int16) - 175, 0).astype(np.uint8)
            glow = cv2.GaussianBlur(bright, (0, 0), 5)
            glow = cv2.resize(glow, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR)
            img = cv2.addWeighted(img, 1.0, glow, bloom_strength * 0.85, 0)

        # HDR-style local tone mapping approximation (not HDR display output).
        exposure = int(s["exposure"])
        if exposure:
            f = img.astype(np.float32) * (1.0 + exposure / 100.0)
            img = np.clip((f * 255.0) / (f + 55.0), 0, 255).astype(np.uint8)

        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[:, :, 1] = np.clip(hsv[:, :, 1] * (s["saturation"] / 100.0), 0, 255)
        img = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

        warmth = int(s["warmth"])
        if warmth:
            f = img.astype(np.float32)
            f[:, :, 2] += warmth * 0.65
            f[:, :, 0] -= warmth * 0.45
            img = np.clip(f, 0, 255).astype(np.uint8)

        # Vignette, calculated at the processing resolution.
        vignette = max(0, min(100, int(s["vignette"]))) / 100.0
        if vignette:
            hh, ww = img.shape[:2]
            y, x = np.ogrid[-1:1:complex(hh), -1:1:complex(ww)]
            mask = np.clip(1.0 - (x*x + y*y) * vignette * 0.42, 0.55, 1.0)
            img = np.clip(img.astype(np.float32) * mask[..., None], 0, 255).astype(np.uint8)

        sharp = max(0, min(100, int(s["sharpness"]))) / 100.0
        if sharp > 0.001:
            blur = cv2.GaussianBlur(img, (0, 0), 1.0)
            img = cv2.addWeighted(img, 1.0 + sharp*0.55, blur, -sharp*0.55, 0)

        # Chromatic aberration is optional and deliberately capped.
        chroma = max(0, min(5, int(s["chromatic"])))
        if chroma:
            b, g, r = cv2.split(img)
            b = np.roll(b, chroma, axis=1)
            r = np.roll(r, -chroma, axis=1)
            img = cv2.merge((b, g, r))

        if scale < 0.99:
            img = cv2.resize(img, (w, h), interpolation=cv2.INTER_LINEAR)
        return img


class FrameBus(QObject):
    frame = Signal(object, object)
    error = Signal(str)


class WindowCaptureWorker(threading.Thread):
    def __init__(self, hwnd, engine, bus, overlay_hwnd):
        super().__init__(daemon=True)
        self.hwnd, self.engine, self.bus = hwnd, engine, bus
        self.overlay_hwnd = overlay_hwnd
        self.running = True

    def stop(self):
        self.running = False

    def run(self):
        try:
            with mss.mss() as sct:
                while self.running:
                    start = time.perf_counter()
                    if not win32gui.IsWindow(self.hwnd) or win32gui.IsIconic(self.hwnd):
                        time.sleep(0.15)
                        continue
                    left, top, right, bottom = win32gui.GetWindowRect(self.hwnd)
                    width, height = right-left, bottom-top
                    if width < 2 or height < 2:
                        time.sleep(0.1)
                        continue
                    # Capture monitor region containing the selected window, then crop.
                    mon = sct.monitors[0]
                    ml, mt = mon["left"], mon["top"]
                    cl = max(left, ml); ct = max(top, mt)
                    cr = min(right, ml + mon["width"]); cb = min(bottom, mt + mon["height"])
                    if cr <= cl or cb <= ct:
                        time.sleep(0.1)
                        continue
                    # Temporarily hide our own overlay while taking the desktop
                    # frame. WDA_EXCLUDEFROMCAPTURE can produce a black rectangle
                    # in MSS captures on some Windows/GPU combinations.
                    # Hide/show is done with Win32 and NOACTIVATE so Roblox keeps focus.
                    hidden = False
                    try:
                        if self.overlay_hwnd and user32.IsWindow(self.overlay_hwnd):
                            user32.ShowWindow(self.overlay_hwnd, 0)  # SW_HIDE
                            hidden = True
                        shot = np.asarray(sct.grab({
                            "left": cl, "top": ct, "width": cr-cl, "height": cb-ct
                        }))
                    finally:
                        if hidden and self.overlay_hwnd and user32.IsWindow(self.overlay_hwnd):
                            user32.ShowWindow(self.overlay_hwnd, 4)  # SW_SHOWNOACTIVATE
                    frame = cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)
                    filtered = self.engine.apply(frame)
                    # Position in virtual desktop coordinates.
                    self.bus.frame.emit(filtered, (cl, ct, cr-cl, cb-ct))
                    fps = max(5, min(240, int(self.engine.settings["fps"])))
                    time.sleep(max(0.0, 1.0/fps - (time.perf_counter()-start)))
        except Exception as exc:
            self.bus.error.emit(str(exc))


class Overlay(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Tool |
                            Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("background:#080b10;")
        self.bus = FrameBus()
        self.bus.frame.connect(self.show_frame)
        self.bus.error.connect(self.error)
        self.engine = EffectEngine()
        self.worker = None
        self.active = False
        self.panel = None

    def show_frame(self, frame, rect):
        x, y, w, h = rect
        # Keep overlay aligned to the captured client-visible rectangle.
        self.setGeometry(x, y, w, h)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.shape[1]*3,
                      QImage.Format.Format_RGB888).copy()
        self.label.setGeometry(self.rect())
        self.label.setPixmap(QPixmap.fromImage(qimg).scaled(
            self.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation))
        if not self.isVisible():
            self.show()
            # Click-through overlay so mouse input still reaches Roblox.
            try:
                hwnd_overlay = int(self.winId())
                exstyle = user32.GetWindowLongW(hwnd_overlay, -20)
                # WS_EX_TRANSPARENT = click-through; WS_EX_NOACTIVATE = never steal focus.
                user32.SetWindowLongW(hwnd_overlay, -20, exstyle | 0x00000020 | 0x00000080 | 0x08000000)
                user32.SetWindowPos(hwnd_overlay, -1, 0, 0, 0, 0,
                                    0x0001 | 0x0002 | 0x0010 | 0x0040)
            except Exception:
                pass

    def start(self, hwnd):
        if self.active:
            self.stop()
        self.active = True
        self.worker = WindowCaptureWorker(hwnd, self.engine, self.bus, int(self.winId()))
        self.worker.start()

    def stop(self):
        self.active = False
        if self.worker:
            self.worker.stop()
            self.worker.join(timeout=2)
            self.worker = None
        self.hide()

    def error(self, message):
        self.stop()
        QMessageBox.critical(self.panel, "Capture error", message)

    def closeEvent(self, event):
        self.stop()
        event.accept()


class MainPanel(QWidget):
    def __init__(self, overlay):
        super().__init__()
        self.overlay = overlay
        overlay.panel = self
        self.setWindowTitle("RBloxTrex")
        self.setMinimumWidth(440)
        self.setStyleSheet("""
            QWidget { background:#fff7fb; color:#542b43; font-family:Segoe UI; font-size:10pt; }
            QGroupBox { background:#ffffff; border:1px solid #f2c9dc; border-radius:12px; margin-top:10px; padding:10px; font-weight:700; }
            QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 6px; color:#c64f8c; }
            QPushButton { background:#e978ad; color:white; border:0; border-radius:9px; padding:10px; font-weight:700; }
            QPushButton:hover { background:#d85c98; }
            QPushButton:pressed { background:#bd477f; padding-top:11px; padding-bottom:9px; }
            QPushButton#secondary { background:#f7dce9; color:#8b3e67; }
            QPushButton#secondary:hover { background:#f2c6dc; }
            QComboBox { background:#fff; color:#542b43; padding:8px; border:1px solid #efbfd5; border-radius:7px; }
            QComboBox QAbstractItemView { background:#fff; selection-background-color:#f5c6dc; color:#542b43; }
            QSlider::groove:horizontal { height:6px; background:#f2d8e5; border-radius:3px; }
            QSlider::handle:horizontal { background:#df6da4; border:2px solid white; width:15px; height:15px; margin:-6px 0; border-radius:8px; }
            QLabel#muted { color:#a47791; font-size:9pt; }
        """)
        layout = QVBoxLayout(self)
        title = QLabel("RBloxTrex  <span style='color:#df6da4'>PINK EDITION</span>")
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setStyleSheet("font-size:19pt;font-weight:800;color:#6b3654;")
        layout.addWidget(title)
        sub = QLabel("Window-specific post-processing • Low-end PC mode")
        sub.setObjectName("muted")
        layout.addWidget(sub)

        win_group = QGroupBox("Target window")
        win_layout = QVBoxLayout(win_group)
        row = QHBoxLayout()
        self.windows = QComboBox()
        self.refresh_btn = QPushButton("Refresh windows")
        self.refresh_btn.setObjectName("secondary")
        self.refresh_btn.clicked.connect(self.refresh_windows)
        row.addWidget(self.windows, 1); row.addWidget(self.refresh_btn)
        win_layout.addLayout(row)
        layout.addWidget(win_group)

        self.sliders = {}
        group = QGroupBox("Effects")
        form = QFormLayout(group)
        specs = [
            ("bloom", "Bloom", 0, 70, 22),
            ("exposure", "HDR-style exposure", 0, 40, 8),
            ("brightness", "Brightness", -40, 40, 2),
            ("contrast", "Contrast", 70, 150, 116),
            ("saturation", "Saturation", 0, 160, 108),
            ("warmth", "Warmth", -30, 30, 7),
            ("sharpness", "Sharpness", 0, 50, 12),
            ("vignette", "Vignette", 0, 60, 8),
            ("chromatic", "Chromatic aberration", 0, 5, 0),
        ]
        for key, label, lo, hi, default in specs:
            roww = QWidget(); rlayout = QHBoxLayout(roww); rlayout.setContentsMargins(0,0,0,0)
            slider = QSlider(Qt.Orientation.Horizontal); slider.setRange(lo,hi); slider.setValue(default)
            value = QLabel(str(default)); value.setMinimumWidth(30)
            slider.valueChanged.connect(lambda v,k=key,l=value: self.set_value(k,v,l))
            rlayout.addWidget(slider); rlayout.addWidget(value)
            form.addRow(label, roww); self.sliders[key] = slider
        layout.addWidget(group)

        perf = QGroupBox("Performance")
        pform = QFormLayout(perf)
        self.fps = QComboBox()
        for v in (15, 20, 30, 60, 90, 120, 144, 165, 180, 200, 240): self.fps.addItem(f"{v} FPS", v)
        self.fps.setCurrentIndex(0)
        self.fps.currentIndexChanged.connect(lambda _: self.set_setting("fps", self.fps.currentData()))
        self.scale = QComboBox()
        for label, val in (("Half resolution (fastest)",50),("75% resolution",75),("Full resolution (best quality)",100)):
            self.scale.addItem(label,val)
        self.scale.currentIndexChanged.connect(lambda _: self.set_setting("scale", self.scale.currentData()))
        pform.addRow("Capture rate", self.fps); pform.addRow("Processing resolution", self.scale)
        layout.addWidget(perf)

        actions = QHBoxLayout()
        self.preset = QComboBox()
        self.preset.addItems(["Low-end (GT 1030)", "Realistic", "Cinematic"])
        self.preset.currentIndexChanged.connect(self.apply_preset)
        actions.addWidget(self.preset, 1)
        apply_btn = QPushButton("Apply preset"); apply_btn.clicked.connect(lambda: self.apply_preset())
        actions.addWidget(apply_btn)
        layout.addLayout(actions)

        btns = QHBoxLayout()
        launch = QPushButton("Launch Roblox + Shader")
        launch.clicked.connect(self.launch_roblox)
        start = QPushButton("Start on selected window"); start.clicked.connect(self.start)
        stop = QPushButton("Stop overlay"); stop.setObjectName("secondary"); stop.clicked.connect(self.overlay.stop)
        btns.addWidget(launch); btns.addWidget(start); btns.addWidget(stop); layout.addLayout(btns)

        note = QLabel("F8: hide/show overlay  •  F9: stop\nUse windowed or borderless mode. Exclusive fullscreen is not supported.\nRBloxTrex excludes its own overlay from capture to prevent recursive frame feedback. High FPS values are targets and depend on capture speed and GPU.")
        note.setObjectName("muted"); note.setWordWrap(True); layout.addWidget(note)
        QShortcut(QKeySequence("F8"), self).activated.connect(self.toggle)
        QShortcut(QKeySequence("F9"), self).activated.connect(self.overlay.stop)

        # Soft click sounds; create a tiny original UI tone locally (no external assets).
        self._sound_file = APP_DIR / "ui_click.wav"
        try:
            if not self._sound_file.exists():
                rate, duration = 22050, 0.055
                with wave.open(str(self._sound_file), "wb") as wav:
                    wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate)
                    samples = []
                    for i in range(int(rate * duration)):
                        envelope = max(0.0, 1.0 - i / (rate * duration))
                        sample = int(4500 * envelope * math.sin(2 * math.pi * 740 * i / rate))
                        samples.append(struct.pack("<h", sample))
                    wav.writeframes(b"".join(samples))
            self._click_sound = QSoundEffect(self)
            self._click_sound.setSource(QUrl.fromLocalFile(str(self._sound_file)))
            self._click_sound.setVolume(0.16)
            for button in self.findChildren(QPushButton):
                button.clicked.connect(self.play_click)
        except Exception:
            self._click_sound = None
        self._fade_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fade_effect)
        self._fade_animation = QPropertyAnimation(self._fade_effect, b"opacity", self)
        self._fade_animation.setDuration(260)
        self._fade_animation.setStartValue(0.0)
        self._fade_animation.setEndValue(1.0)
        self._fade_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_animation.start()
        self.refresh_windows()
        self.load()

    def play_click(self):
        try:
            if self._click_sound:
                self._click_sound.stop()
                self._click_sound.play()
        except Exception:
            pass

    def refresh_windows(self):
        current = self.windows.currentData()
        self.windows.clear()
        for hwnd, title in list_windows():
            self.windows.addItem(title, hwnd)
        if current:
            idx = self.windows.findData(current)
            if idx >= 0: self.windows.setCurrentIndex(idx)

    def set_value(self, key, value, label):
        label.setText(str(value)); self.set_setting(key, value)

    def set_setting(self, key, value):
        self.overlay.engine.settings[key] = int(value)
        self.save()

    def apply_preset(self, *_):
        name = self.preset.currentText()
        presets = {
            "Low-end (GT 1030)": dict(bloom=12, exposure=5, brightness=1, contrast=110, saturation=105, warmth=4, sharpness=5, vignette=5, chromatic=0, fps=15, scale=50),
            "Realistic": dict(bloom=24, exposure=10, brightness=2, contrast=116, saturation=108, warmth=7, sharpness=12, vignette=9, chromatic=0, fps=20, scale=75),
            "Cinematic": dict(bloom=35, exposure=14, brightness=0, contrast=125, saturation=112, warmth=10, sharpness=16, vignette=20, chromatic=1, fps=20, scale=75),
        }
        for k,v in presets[name].items():
            self.overlay.engine.settings[k] = v
            if k in self.sliders: self.sliders[k].setValue(v)
        self.fps.setCurrentIndex(max(0, self.fps.findData(presets[name]["fps"])))
        self.scale.setCurrentIndex(max(0, self.scale.findData(presets[name]["scale"])))
        self.save()

    def launch_roblox(self):
        # Prefer Roblox's registered protocol handler; fall back to installed player executable.
        launched = False
        try:
            os.startfile("roblox://")
            launched = True
        except Exception:
            pass
        if not launched:
            versions_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Roblox" / "Versions"
            candidates = sorted(versions_dir.glob("version-*/RobloxPlayerBeta.exe"),
                                key=lambda p: p.stat().st_mtime if p.exists() else 0,
                                reverse=True)
            if candidates:
                try:
                    # Use the standard Windows shell launcher instead of spawning a command process.
                    os.startfile(str(candidates[0]))
                    launched = True
                except Exception as exc:
                    QMessageBox.critical(self, "Roblox launch failed", str(exc))
        if not launched:
            QMessageBox.warning(
                self, "Roblox not found",
                "Could not find the Roblox launcher. Install Roblox from roblox.com, sign in, "
                "then try again. You can also launch a Roblox experience in your browser first."
            )
            return
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("Waiting for Roblox…")
        from PySide6.QtCore import QTimer
        def find_roblox():
            self.refresh_windows()
            for i in range(self.windows.count()):
                label = self.windows.itemText(i).lower()
                if "robloxplayerbeta" in label or "roblox" in label:
                    self.windows.setCurrentIndex(i)
                    self.start()
                    self.refresh_btn.setText("Refresh windows")
                    self.refresh_btn.setEnabled(True)
                    return
            self.refresh_btn.setText("Still waiting…")
            QTimer.singleShot(1800, find_roblox)
        QTimer.singleShot(2200, find_roblox)

    def start(self):
        hwnd = self.windows.currentData()
        if not hwnd:
            QMessageBox.information(self, "Select a window", "Click Refresh windows and select a visible target window.")
            return
        if not win32gui.IsWindow(hwnd):
            self.refresh_windows()
            QMessageBox.warning(self, "Window unavailable", "That window is no longer available. Refresh the list.")
            return
        self.overlay.start(hwnd)

    def toggle(self):
        if self.overlay.isVisible(): self.overlay.hide()
        elif self.overlay.active: self.overlay.show()

    def save(self):
        try: CONFIG.write_text(json.dumps(self.overlay.engine.settings, indent=2))
        except Exception: pass

    def load(self):
        try:
            data = json.loads(CONFIG.read_text())
            self.overlay.engine.settings.update(data)
            for k, slider in self.sliders.items():
                if k in data: slider.setValue(int(data[k]))
            self.fps.setCurrentIndex(max(0,self.fps.findData(int(data.get("fps",15)))))
            self.scale.setCurrentIndex(max(0,self.scale.findData(int(data.get("scale",50)))))
            name = data.get("preset")
            if name:
                i=self.preset.findText(name)
                if i>=0: self.preset.setCurrentIndex(i)
        except Exception: pass

    def closeEvent(self, event):
        self.overlay.stop(); event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("RBloxTrex")
    icon_path = Path(__file__).resolve().parent.parent / "RBloxTrex.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    overlay = Overlay()
    panel = MainPanel(overlay)
    panel.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
