#!/usr/bin/env python3
"""Guide-button overlay daemon for the BC-250 console setup.

Watches a gamepad's Guide/Xbox (BTN_MODE) button directly at the evdev level
(so it works even while an emulator has exclusive input focus). On a ~0.5s
hold, shows a controller-navigable overlay: Resume, Exit Game, Home, Restart,
Shut Down.

"The current game" is found by walking Pegasus's own process tree and picking
the heaviest descendant by CPU usage - avoids depending on window-manager
APIs entirely, and works identically for native emulators and Proton games.
"""
import os
import re
import signal
import subprocess
import sys
import threading
import time

import evdev
import psutil
from evdev import ecodes
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QSize
from PySide6.QtGui import QKeyEvent, QIcon, QPainter, QLinearGradient, QColor
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel

HOLD_SECONDS = 0.5
PEGASUS_BIN_NAME = "pegasus-fe"


def find_gamepads():
    devices = [evdev.InputDevice(p) for p in evdev.list_devices()]
    pads = []
    for d in devices:
        caps = d.capabilities().get(ecodes.EV_KEY, [])
        if ecodes.BTN_MODE in caps or ecodes.BTN_GAMEPAD in caps or ecodes.BTN_A in caps:
            pads.append(d)
    return pads


def find_pegasus_pid():
    for p in psutil.process_iter(["pid", "name", "exe"]):
        try:
            name = p.info["name"] or ""
            exe = p.info["exe"] or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if PEGASUS_BIN_NAME in name or PEGASUS_BIN_NAME in exe:
            return p.info["pid"]
    return None


def find_game_process():
    """Heaviest descendant of pegasus-fe, i.e. the actual running game.

    Uses cumulative CPU time (cpu_times), not an interval-sampled cpu_percent -
    cpu_times is a running total since the process started and is available
    instantly with no sampling window, unlike cpu_percent(interval=...) which
    needs to sleep across two samples to compute a rate. The game process has
    been accumulating real CPU time throughout play; wrapper/shell processes
    in the launch chain have accumulated almost none - no sleep needed to
    tell them apart.
    """
    pegasus_pid = find_pegasus_pid()
    if pegasus_pid is None:
        return None
    try:
        pegasus = psutil.Process(pegasus_pid)
        descendants = pegasus.children(recursive=True)
    except psutil.NoSuchProcess:
        return None
    if not descendants:
        return None

    best, best_time = None, -1.0
    for p in descendants:
        try:
            t = p.cpu_times()
            total = t.user + t.system
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if total > best_time:
            best, best_time = p, total
    return best


def kill_process_tree(proc, grace=3.0):
    if proc is None:
        return
    try:
        children = proc.children(recursive=True)
    except psutil.NoSuchProcess:
        return
    procs = children + [proc]
    for p in procs:
        try:
            p.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    gone, alive = psutil.wait_procs(procs, timeout=grace)
    for p in alive:
        try:
            p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


class GuideWatcher(QObject):
    triggered = Signal()
    nav_prev = Signal()
    nav_next = Signal()
    nav_confirm = Signal()
    nav_cancel = Signal()

    def __init__(self, devices):
        super().__init__()
        self.devices = devices
        self._pressed_since = {}

    def run(self):
        """One blocking-read thread per device - read_loop() blocks in the
        kernel until an event actually arrives, so input is handled the
        instant it happens instead of waiting on a polling interval."""
        threads = [
            threading.Thread(target=self._watch_device, args=(d,), daemon=True)
            for d in self.devices
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def _watch_device(self, device):
        try:
            for event in device.read_loop():
                self._handle(device, event)
        except (OSError, evdev.eventio.EvdevError):
            pass

    def _handle(self, device, event):
        if event.type == ecodes.EV_KEY and event.code == ecodes.BTN_MODE:
            if event.value == 1:
                self._pressed_since[device.path] = time.time()
                threading.Thread(target=self._check_hold, args=(device.path,), daemon=True).start()
            elif event.value == 0:
                self._pressed_since.pop(device.path, None)
        elif event.type == ecodes.EV_ABS and event.code == ecodes.ABS_HAT0X:
            if event.value < 0:
                self.nav_prev.emit()
            elif event.value > 0:
                self.nav_next.emit()
        elif event.type == ecodes.EV_KEY and event.value == 1:
            if event.code == ecodes.BTN_A or event.code == ecodes.BTN_SOUTH:
                self.nav_confirm.emit()
            elif event.code == ecodes.BTN_B or event.code == ecodes.BTN_EAST:
                self.nav_cancel.emit()

    def _check_hold(self, path):
        time.sleep(HOLD_SECONDS)
        if path in self._pressed_since:
            self.triggered.emit()


ICON_SIZE = 72
ITEM_BOX = 108

ACTIONS = [
    ("Resume", "media-playback-start"),
    ("Exit Game", "process-stop"),
    ("Home", "go-home"),
    ("Restart Console", "view-refresh"),
    ("Shut Down", "system-shutdown"),
]


class IconItem(QWidget):
    def __init__(self, icon_name):
        super().__init__()
        self.setFixedSize(ITEM_BOX, ITEM_BOX)
        self.setObjectName("iconItem")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        icon_label = QLabel()
        icon_label.setPixmap(QIcon.fromTheme(icon_name).pixmap(QSize(ICON_SIZE, ICON_SIZE)))
        icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon_label)
        self.set_selected(False)

    def set_selected(self, selected):
        self.setStyleSheet(
            "#iconItem { background-color: %s; border-radius: 16px; }"
            % ("#3daee9" if selected else "transparent")
        )


STRIPE_HEIGHT = 300


class OverlayMenu(QWidget):
    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("""
            QLabel#caption { color: white; font-size: 24px; font-weight: 600; }
        """)
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(0, 0, screen.width(), screen.height())
        self._stripe_center_y = screen.height() // 2 - 20

        self.current = 0
        self.items = [IconItem(icon_name) for _, icon_name in ACTIONS]

        row = QWidget(self)
        row_layout = QHBoxLayout(row)
        row_layout.setSpacing(24)
        for item in self.items:
            row_layout.addWidget(item)
        row.adjustSize()
        row.move((screen.width() - row.width()) // 2, screen.height() // 2 - row.height() // 2 - 40)

        self.caption = QLabel(self)
        self.caption.setObjectName("caption")
        self.caption.setAlignment(Qt.AlignCenter)
        self.caption.adjustSize()

        self._reposition_caption(screen)
        self.hide()

    def paintEvent(self, event):
        """Full-width dark stripe behind the menu, transparent with a quick
        fade in/out at the top and bottom edges rather than a hard-edged bar."""
        painter = QPainter(self)
        top = self._stripe_center_y - STRIPE_HEIGHT // 2
        gradient = QLinearGradient(0, top, 0, top + STRIPE_HEIGHT)
        dark = QColor(10, 10, 18, 215)
        transparent = QColor(10, 10, 18, 0)
        gradient.setColorAt(0.0, transparent)
        gradient.setColorAt(0.18, dark)
        gradient.setColorAt(0.82, dark)
        gradient.setColorAt(1.0, transparent)
        painter.fillRect(0, top, self.width(), STRIPE_HEIGHT, gradient)

    def _reposition_caption(self, screen):
        self.caption.adjustSize()
        self.caption.move(
            (screen.width() - self.caption.width()) // 2,
            screen.height() // 2 + ITEM_BOX // 2,
        )

    def _refresh(self):
        for i, item in enumerate(self.items):
            item.set_selected(i == self.current)
        name, _ = ACTIONS[self.current]
        self.caption.setText(name)
        self._reposition_caption(QApplication.primaryScreen().geometry())

    def show_menu(self):
        self.current = 0
        self._refresh()
        self.showFullScreen()
        self.raise_()
        self.activateWindow()

    def nav_prev_action(self):
        if self.isVisible():
            self.current = max(0, self.current - 1)
            self._refresh()

    def nav_next_action(self):
        if self.isVisible():
            self.current = min(len(ACTIONS) - 1, self.current + 1)
            self._refresh()

    def nav_confirm_action(self):
        if self.isVisible():
            self._activate()

    def nav_cancel_action(self):
        if self.isVisible():
            self.hide()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Left:
            self.nav_prev_action()
        elif event.key() == Qt.Key_Right:
            self.nav_next_action()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._activate()
        elif event.key() == Qt.Key_Escape:
            self.hide()

    def _activate(self):
        action, _ = ACTIONS[self.current]
        self.hide()
        if action == "Resume":
            return
        elif action == "Exit Game":
            proc = find_game_process()
            kill_process_tree(proc)
        elif action == "Home":
            proc = find_game_process()
            kill_process_tree(proc)
        elif action == "Restart Console":
            subprocess.Popen(["systemctl", "reboot"])
        elif action == "Shut Down":
            subprocess.Popen(["systemctl", "poweroff"])


def main():
    app = QApplication(sys.argv)
    overlay = OverlayMenu()

    pads = find_gamepads()
    if not pads:
        print("No gamepad found.", file=sys.stderr)
        sys.exit(1)
    print(f"Watching {len(pads)} device(s): {[d.name for d in pads]}")

    watcher = GuideWatcher(pads)
    watcher.triggered.connect(overlay.show_menu)
    watcher.nav_prev.connect(overlay.nav_prev_action)
    watcher.nav_next.connect(overlay.nav_next_action)
    watcher.nav_confirm.connect(overlay.nav_confirm_action)
    watcher.nav_cancel.connect(overlay.nav_cancel_action)
    t = threading.Thread(target=watcher.run, daemon=True)
    t.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
