#!/usr/bin/env python3
"""Guide-button overlay daemon for the BC-250 console setup.

Watches a gamepad's Guide/Xbox (BTN_MODE) button directly at the evdev level
(so it works even while an emulator has exclusive input focus). On a
HOLD_SECONDS hold, shows a controller/mouse-navigable overlay whose items
are defined in MENU_ITEMS below - see that section to add or remove one.

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
from dataclasses import dataclass
from typing import Callable

import evdev
import psutil
from evdev import ecodes
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QSize
from PySide6.QtGui import QKeyEvent, QIcon, QPainter, QLinearGradient, QColor
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel

HOLD_SECONDS = 2.0
PEGASUS_BIN_NAME = "pegasus-fe"
DEFAULT_ACCENT = "#3daee9"
STICK_DEADZONE = 16000  # ~50% of a typical -32768..32767 axis range

PEGASUS_CONFIG_DIR = os.path.expanduser("~/.config/pegasus-frontend")
PEGASUS_SETTINGS = os.path.join(PEGASUS_CONFIG_DIR, "settings.txt")


def get_pegasus_accent_color():
    """Reads the currently active Pegasus theme's accent color straight out
    of its theme.qml, so changing the Pegasus theme changes the guide-menu's
    color too - re-read fresh every time the menu opens, not cached, so a
    theme change takes effect on the very next guide-button press with no
    restart needed. Falls back to DEFAULT_ACCENT if anything about this
    doesn't match gameOS's particular theme.qml structure (a different
    theme isn't guaranteed to define its colors the same way)."""
    try:
        with open(PEGASUS_SETTINGS, "r", encoding="utf-8") as f:
            settings = f.read()
        m = re.search(r"^general\.theme:\s*(\S+)", settings, re.MULTILINE)
        if not m:
            return DEFAULT_ACCENT
        theme_path = os.path.join(PEGASUS_CONFIG_DIR, m.group(1), "theme.qml")

        with open(theme_path, "r", encoding="utf-8") as f:
            theme_qml = f.read()
        m = re.search(r"accent:\s*\"(#[0-9a-fA-F]{3,8})\"", theme_qml)
        if not m:
            return DEFAULT_ACCENT
        return m.group(1)
    except OSError:
        return DEFAULT_ACCENT


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
        self._stick_zone = {}

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
        elif event.type == ecodes.EV_ABS and event.code == ecodes.ABS_X:
            # Left stick, in addition to the D-pad. Edge-triggered off a
            # deadzone (fires once per crossing, not repeatedly while held
            # over at the extreme) so it behaves like a single D-pad press,
            # not a fast-repeat scroll.
            if event.value < -STICK_DEADZONE:
                zone = -1
            elif event.value > STICK_DEADZONE:
                zone = 1
            else:
                zone = 0
            if zone != self._stick_zone.get(device.path, 0):
                self._stick_zone[device.path] = zone
                if zone == -1:
                    self.nav_prev.emit()
                elif zone == 1:
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


@dataclass
class MenuItem:
    name: str
    icon: str
    action: Callable[[], None]


# --- Menu item modules --------------------------------------------------
# Each item is self-contained: a name, a system theme icon, and its own
# action function. To remove an item from the menu, comment out its line
# in MENU_ITEMS below (leave its function defined so it's a one-line
# change to bring back). To add one, write a new _action_* function and
# append a MenuItem for it - nothing else needs to change; the icon row
# and its centering automatically adjust to however many items are active.

def _action_resume():
    pass  # hiding the overlay (done by the caller before dispatch) is all "resume" needs to do


def _action_exit_game():
    proc = find_game_process()
    kill_process_tree(proc)


def _action_home():
    """Currently identical to Exit Game - both just close the running game,
    which is enough to reveal Pegasus again since it's never actually
    covered/closed, only hidden behind the game's fullscreen window. Left
    here so it's easy to give this a real distinct behavior later (e.g.
    jumping Pegasus back to its actual home screen) instead of deleting it."""
    proc = find_game_process()
    kill_process_tree(proc)


def _action_restart_console():
    subprocess.Popen(["systemctl", "reboot"])


def _action_shutdown():
    subprocess.Popen(["systemctl", "poweroff"])


MENU_ITEMS = [
    MenuItem("Resume", "media-playback-start", _action_resume),
    MenuItem("Exit Game", "process-stop", _action_exit_game),
    # MenuItem("Home", "go-home", _action_home),  # disabled 2026-09-12 - identical to Exit Game right now, see _action_home()
    MenuItem("Restart Console", "view-refresh", _action_restart_console),
    MenuItem("Shut Down", "system-shutdown", _action_shutdown),
]


def white_bold_icon(icon_name, size):
    """Recolor a theme icon to a single flat white, with a cheap 1px dilate
    (stamping the silhouette at a few sub-pixel offsets) for a slightly
    bolder look - theme icons otherwise vary wildly in their own colors
    (e.g. process-stop is red, go-home is multi-tone) which reads as
    inconsistent at a glance."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QPixmap

    source = QIcon.fromTheme(icon_name).pixmap(QSize(size, size))
    silhouette = QPixmap(source.size())
    silhouette.fill(Qt.transparent)
    p = QPainter(silhouette)
    p.drawPixmap(0, 0, source)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(silhouette.rect(), Qt.white)
    p.end()

    bold = QPixmap(source.size())
    bold.fill(Qt.transparent)
    p = QPainter(bold)
    for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
        p.drawPixmap(QPoint(dx, dy), silhouette)
    p.end()
    return bold


class IconItem(QWidget):
    hovered = Signal()
    clicked = Signal()

    def __init__(self, icon_name):
        super().__init__()
        self.setFixedSize(ITEM_BOX, ITEM_BOX)
        self.setObjectName("iconItem")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        icon_label = QLabel()
        icon_label.setPixmap(white_bold_icon(icon_name, ICON_SIZE))
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(icon_label)
        self.set_selected(False)

    def set_selected(self, selected, accent_color=DEFAULT_ACCENT):
        self.setStyleSheet(
            "#iconItem { background-color: %s; border-radius: 16px; }"
            % (accent_color if selected else "transparent")
        )

    def enterEvent(self, event):
        self.hovered.emit()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()


STRIPE_HEIGHT = 480


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

        self.accent_color = DEFAULT_ACCENT
        self.current = 0
        self.items = [IconItem(item.icon) for item in MENU_ITEMS]
        for i, item in enumerate(self.items):
            # Hover previews the selection (matches gamepad D-pad behavior);
            # a click both selects and immediately confirms it - this is
            # still a PC, mouse should be a first-class option alongside
            # gamepad navigation, not an afterthought.
            item.hovered.connect(lambda idx=i: self._set_current(idx))
            item.clicked.connect(lambda idx=i: self._activate_index(idx))

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
        """Full-width dark stripe behind the menu. Soft, gradual fade in/out
        over a larger portion of the stripe's own height, fully transparent
        well before reaching the stripe's own top/bottom (which themselves
        sit well inside the real screen edges, never touching them)."""
        painter = QPainter(self)
        top = self._stripe_center_y - STRIPE_HEIGHT // 2
        gradient = QLinearGradient(0, top, 0, top + STRIPE_HEIGHT)
        dark = QColor(10, 10, 18, 183)
        transparent = QColor(10, 10, 18, 0)
        gradient.setColorAt(0.0, transparent)
        gradient.setColorAt(0.38, dark)
        gradient.setColorAt(0.62, dark)
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
            item.set_selected(i == self.current, self.accent_color)
        name = MENU_ITEMS[self.current].name
        self.caption.setText(name)
        self._reposition_caption(QApplication.primaryScreen().geometry())

    def show_menu(self):
        # Read fresh each time the menu opens (not cached) - changing the
        # Pegasus theme takes effect on the very next guide-button press.
        self.accent_color = get_pegasus_accent_color()
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
            self.current = min(len(MENU_ITEMS) - 1, self.current + 1)
            self._refresh()

    def nav_confirm_action(self):
        if self.isVisible():
            self._activate()

    def nav_cancel_action(self):
        if self.isVisible():
            self.hide()

    def _set_current(self, index):
        if self.isVisible():
            self.current = index
            self._refresh()

    def _activate_index(self, index):
        if self.isVisible():
            self.current = index
            self._activate()

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
        item = MENU_ITEMS[self.current]
        self.hide()
        item.action()


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
