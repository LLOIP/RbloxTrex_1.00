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

APP_DIR = Path.home() / "AppData" / "Local" / "ResolShaders"
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
        if any(x in title.lower() for x in ("resolshaders", "rbloxtrex")):
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
            "fps": 30, "scale": 50, "preset": "Low-end (GT 1030)"
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
    error = Signal(str)


class WindowCaptureWorker(threading.Thread):
    """Captures the selected application window through Windows Graphics Capture.

    Unlike desktop-region capture, WGC captures the target window directly. The
    overlay never needs to hide/show itself, which removes the visible flicker and
    avoids recursively capturing the processed overlay.
    """
    def __init__(self, hwnd, title, engine, overlay):
        super().__init__(daemon=True)
        self.hwnd = hwnd
        self.title = title
        self.engine = engine
        self.overlay = overlay
        self.running = True
        self.capture = None
        self.control = None

    def stop(self):
        self.running = False
        try:
            if self.control is not None:
                self.control.stop()
        except Exception:
            pass

    def run(self):
        try:
            from windows_capture import WindowsCapture
            fps = max(5, min(120, int(self.engine.settings.get("fps", 30))))
            interval = max(1, int(1000 / fps))
            capture = WindowsCapture(
                cursor_capture=None,
                draw_border=None,
                monitor_index=None,
                window_hwnd=int(self.hwnd),
                minimum_update_interval=interval,
            )
            self.capture = capture

            @capture.event
            def on_frame_arrived(frame, capture_control):
                if not self.running:
                    capture_control.stop()
                    return
                self.control = capture_control
                try:
                    # The library exposes WGC frame data as a NumPy-compatible BGRA array.
                    if hasattr(frame, "to_bgr"):
                        image = frame.to_bgr()
                    else:
                        raw = frame.to_numpy()
                        if raw is None or getattr(raw, "ndim", 0) != 3 or raw.shape[0] < 2 or raw.shape[1] < 2:
                            return
                        image = cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR) if raw.shape[2] == 4 else raw.copy()
                    if image.ndim != 3 or image.shape[0] < 2 or image.shape[1] < 2:
                        return
                    processed = self.engine.apply(image)
                    if win32gui.IsWindow(self.hwnd):
                        client_left, client_top = win32gui.ClientToScreen(self.hwnd, (0, 0))
                        rect = (client_left, client_top, int(image.shape[1]), int(image.shape[0]))
                    else:
                        capture_control.stop()
                        self.running = False
                        return
                    # Latest-frame slot is bounded: slow UI never accumulates a frame queue.
                    self.overlay.publish_frame(processed, rect)
                except Exception as exc:
                    self.overlay.bus.error.emit(f"Window capture frame failed: {exc}")
                    capture_control.stop()

            @capture.event
            def on_closed():
                if self.running:
                    self.overlay.bus.error.emit("The selected game window closed or Windows stopped providing frames.")

            capture.start()
        except Exception as exc:
            self.overlay.bus.error.emit(
                "Windows Graphics Capture could not start for this window. "
                "Try windowed or borderless mode and make sure the game is visible.\n\n" + str(exc)
            )


class Overlay(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Tool |
                            Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("background:#080b10;")
        self.bus = FrameBus()
        self.bus.error.connect(self.error)
        self.engine = EffectEngine()
        self.worker = None
        self.active = False
        self.panel = None
        self._frame_lock = threading.Lock()
        self._latest_frame = None
        self._last_rect = None
        self._display_timer = QTimer(self)
        self._display_timer.setInterval(8)
        self._display_timer.timeout.connect(self._display_latest)

    def publish_frame(self, frame, rect):
        with self._frame_lock:
            self._latest_frame = (frame, rect)

    def _display_latest(self):
        with self._frame_lock:
            item = self._latest_frame
            self._latest_frame = None
        if item is None:
            return
        frame, rect = item
        x, y, w, h = rect
        if rect != self._last_rect:
            self.setGeometry(x, y, w, h)
            self.label.setGeometry(self.rect())
            self._last_rect = rect
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.shape[1]*3,
                      QImage.Format.Format_RGB888).copy()
        self.label.setPixmap(QPixmap.fromImage(qimg).scaled(
            self.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation))
        if not self.isVisible():
            self.show()
            self._set_click_through()

    def _set_click_through(self):
        try:
            hwnd_overlay = int(self.winId())
            exstyle = user32.GetWindowLongW(hwnd_overlay, -20)
            # WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_LAYERED
            user32.SetWindowLongW(hwnd_overlay, -20, exstyle | 0x00000020 | 0x00000080 | 0x00080000)
            user32.SetWindowPos(hwnd_overlay, -1, 0, 0, 0, 0,
                                0x0001 | 0x0002 | 0x0010 | 0x0040)
        except Exception:
            pass

    def start(self, hwnd, title):
        if self.active:
            self.stop()
        self.active = True
        self._last_rect = None
        with self._frame_lock:
            self._latest_frame = None
        self.worker = WindowCaptureWorker(hwnd, title, self.engine, self)
        self._display_timer.start()
        self.worker.start()

    def stop(self):
        self.active = False
        self._display_timer.stop()
        if self.worker:
            self.worker.stop()
            self.worker.join(timeout=2)
            self.worker = None
        with self._frame_lock:
            self._latest_frame = None
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
        self.setWindowTitle("ResolShaders")
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
        title = QLabel("ResolShaders  <span style='color:#df6da4'>GAME VISUALS</span>")
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setStyleSheet("font-size:19pt;font-weight:800;color:#6b3654;")
        layout.addWidget(title)
        sub = QLabel("Universal window post-processing • Windows 10/11")
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
        self.fps.setCurrentIndex(max(0, self.fps.findData(30)))
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
        launch = QPushButton("Refresh game windows")
        launch.clicked.connect(self.refresh_windows)
        start = QPushButton("Start on selected window"); start.clicked.connect(self.start)
        stop = QPushButton("Stop overlay"); stop.setObjectName("secondary"); stop.clicked.connect(self.overlay.stop)
        btns.addWidget(launch); btns.addWidget(start); btns.addWidget(stop); layout.addLayout(btns)

        note = QLabel("F8: hide/show overlay  •  F9: stop\nUses Windows Graphics Capture for the selected game window without hiding/showing the overlay. Windowed or borderless mode is recommended; protected or exclusive-fullscreen games may block capture. FPS is a target, not a guarantee.")
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
            "Low-end (GT 1030)": dict(bloom=12, exposure=5, brightness=1, contrast=110, saturation=105, warmth=4, sharpness=5, vignette=5, chromatic=0, fps=30, scale=50),
            "Realistic": dict(bloom=24, exposure=10, brightness=2, contrast=116, saturation=108, warmth=7, sharpness=12, vignette=9, chromatic=0, fps=30, scale=75),
            "Cinematic": dict(bloom=35, exposure=14, brightness=0, contrast=125, saturation=112, warmth=10, sharpness=16, vignette=20, chromatic=1, fps=30, scale=75),
        }
        for k,v in presets[name].items():
            self.overlay.engine.settings[k] = v
            if k in self.sliders: self.sliders[k].setValue(v)
        self.fps.setCurrentIndex(max(0, self.fps.findData(presets[name]["fps"])))
        self.scale.setCurrentIndex(max(0, self.scale.findData(presets[name]["scale"])))
        self.save()

    def start(self):
        hwnd = self.windows.currentData()
        if not hwnd:
            QMessageBox.information(self, "Select a window", "Click Refresh windows and select a visible target window.")
            return
        if not win32gui.IsWindow(hwnd):
            self.refresh_windows()
            QMessageBox.warning(self, "Window unavailable", "That window is no longer available. Refresh the list.")
            return
        self.overlay.start(hwnd, self.windows.currentText().split("  —  ")[0].strip())

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
            self.fps.setCurrentIndex(max(0,self.fps.findData(int(data.get("fps",30)))))
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
    app.setApplicationName("ResolShaders")
    icon_path = Path(__file__).resolve().parent.parent / "ResolShaders.ico"
    if not icon_path.exists():
        icon_path = Path(__file__).resolve().parent.parent / "ResolShaders_icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    overlay = Overlay()
    panel = MainPanel(overlay)
    panel.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
