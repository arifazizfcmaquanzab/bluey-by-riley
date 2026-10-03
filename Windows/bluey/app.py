"""Native Qt host with a click-through companion and explicit action approval."""
import base64
import ctypes
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import webbrowser

import keyring
import mss
from PIL import Image
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)

from .actions import APPS, validate
from .protocol import ACTIONS, grid_point
from .server import PhoneServer, local_addresses, mint_token


class Events(QObject):
    packet = Signal(object)
    status = Signal(str)
    token = Signal(object)


class Overlay(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.resize(340, 210)
        self.caption = ''
        self.target = None
        self.show()

    def tick(self):
        screen = QApplication.primaryScreen().geometry()
        pos = self.target or QCursor.pos()
        self.move(max(screen.left(), min(pos.x() + 12, screen.right() - self.width())),
                  max(screen.top(), min(pos.y() + 12, screen.bottom() - self.height())))
        self.update()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#636be8'))
        painter.drawEllipse(0, 0, 68, 64)
        for x in (12, 35):
            painter.setBrush(QColor('white'))
            painter.drawEllipse(x, 12, 22, 27)
            painter.setBrush(QColor('#171c37'))
            painter.drawEllipse(x + 9, 22, 9, 12)
        painter.setPen(QPen(QColor('#171c37'), 3))
        painter.drawArc(24, 39, 22, 13, 180 * 16, 180 * 16)
        if self.caption:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#fff9eb'))
            painter.drawRoundedRect(3, 72, 334, 135, 16, 16)
            painter.setPen(QColor('#171c37'))
            painter.drawText(16, 83, 308, 112, Qt.TextFlag.TextWordWrap, self.caption[:650])


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Bluey for Windows')
        self.resize(620, 470)
        self.events = Events()
        self.events.packet.connect(self.receive)
        self.events.status.connect(self.status_changed)
        self.events.token.connect(self.token_ready)
        self.overlay = Overlay()
        self.awake = False
        self.connected = False
        self.generation = 0
        self.snapshot_at = 0
        self.snapshot_size = None
        self.stopped_until = 0
        self.last_face = 0
        self.token_pending = False
        self.tool_busy = False
        self.status = QLabel('Starting…')
        self.controls = QCheckBox('Allow computer actions, ask me on this PC before each action')
        self.controls.toggled.connect(self.control_changed)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText('OpenAI API key, stored in Windows Credential Manager')
        try:
            self.key.setText(keyring.get_password('Bluey', 'openai') or '')
        except Exception:
            pass
        self.model = QLineEdit('gpt-realtime-2.1')
        save = QPushButton('Save API key')
        save.clicked.connect(self.save_key)
        stop = QPushButton('STOP actions and voice (Ctrl+Alt+S)')
        stop.clicked.connect(self.stop)
        self.address = QComboBox()
        self.address.addItems(local_addresses() or ['127.0.0.1'])
        self.address.currentTextChanged.connect(self.update_code)
        self.code = QLineEdit()
        self.code.setReadOnly(True)
        copy = QPushButton('Copy pairing code')
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.code.text()))
        refresh = QPushButton('Refresh network addresses')
        refresh.clicked.connect(self.refresh_addresses)
        form = QFormLayout()
        form.addRow('API key', self.key)
        form.addRow('Realtime model', self.model)
        form.addRow('PC Wi-Fi IPv4 address', self.address)
        form.addRow('Pairing code', self.code)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Install the Android APK, put both devices on the same private Wi-Fi,\n'
                               'then paste this PC’s pairing code into Android. Keep the code private.'))
        layout.addLayout(form)
        for item in (save, copy, refresh, self.controls, stop, self.status):
            layout.addWidget(item)
        notice = QLabel('Screen images and voice are sent to OpenAI when you ask.\n'
                        'API usage is billed separately from ChatGPT Plus.\n'
                        'Minimize this window before sharing your screen. Only the primary display is supported.')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        directory = Path(os.getenv('LOCALAPPDATA', Path.home())) / 'Bluey'
        self.server = PhoneServer(directory, self.events.packet.emit, self.events.status.emit)
        try:
            self.server.start()
            self.status.setText('Waiting for Android on TCP port ' + str(self.server.port))
        except OSError as error:
            self.status.setText('Cannot start local server: ' + str(error))
        self.update_code()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(33)

    def refresh_addresses(self):
        previous = self.address.currentText()
        self.address.clear()
        self.address.addItems(local_addresses() or ['127.0.0.1'])
        if previous in [self.address.itemText(i) for i in range(self.address.count())]:
            self.address.setCurrentText(previous)
        self.update_code()

    def update_code(self):
        if hasattr(self, 'server'):
            self.code.setText(self.server.pairing_code(self.address.currentText()))

    def save_key(self):
        try:
            keyring.set_password('Bluey', 'openai', self.key.text().strip())
            self.status.setText('API key saved in Windows Credential Manager')
        except Exception:
            self.status.setText('Could not save key securely. It can still be used for this launch.')

    def status_changed(self, value):
        self.status.setText(value)
        self.connected = value == 'Android connected'
        if not self.connected:
            self.generation += 1
            self.awake = False
            self.controls.setChecked(False)
            self.snapshot_at = 0
            self.overlay.caption = ''
            self.overlay.target = None

    def control_changed(self, _):
        self.generation += 1
        self.snapshot_at = 0

    def stop(self):
        self.generation += 1
        self.stopped_until = time.monotonic() + 6
        self.controls.setChecked(False)
        self.awake = False
        self.snapshot_at = 0
        self.overlay.caption = 'Stopped'
        self.overlay.target = None
        self.server.send({'command': 'sleep'})

    def tick(self):
        if sys.platform == 'win32':
            user = ctypes.windll.user32
            if all(user.GetAsyncKeyState(key) & 0x8000 for key in (0x11, 0x12, 0x53)):
                self.stop()
        self.overlay.tick()
        if time.monotonic() - self.last_face > 0.1:
            screen = QApplication.primaryScreen().geometry()
            mouse = QCursor.pos()
            self.server.send({'face': {'gazeX': max(-1, min(1, 2 * (mouse.x() - screen.left()) / screen.width() - 1)),
                                       'gazeY': max(-1, min(1, 2 * (mouse.y() - screen.top()) / screen.height() - 1)),
                                       'mood': 'listening' if self.awake else 'resting', 'talk': 0}})
            self.last_face = time.monotonic()

    def receive(self, packet):
        command = packet.get('command')
        if command == 'realtimeToken':
            if self.token_pending or time.monotonic() < self.stopped_until:
                self.reply(packet, 'Busy or stopped. Try again in a few seconds.', command='error')
                return
            self.token_pending = True
            api_key, model, enabled = self.key.text().strip(), self.model.text().strip(), self.controls.isChecked()
            generation = self.generation
            def work():
                try:
                    token = mint_token(api_key, model, enabled)
                    result = {'command': 'realtimeToken', 'callID': packet.get('callID'), 'text': token, 'model': model}
                except Exception as error:
                    result = {'command': 'error', 'callID': packet.get('callID'), 'text': str(error)}
                self.events.token.emit((generation, result))
            threading.Thread(target=work, daemon=True).start()
        elif command == 'awake':
            self.awake = True
        elif command == 'asleep':
            self.generation += 1
            self.awake = False
            self.snapshot_at = 0
            self.overlay.target = None
            self.overlay.caption = ''
        elif command in ('caption', 'captionDone'):
            self.overlay.caption = str(packet.get('text', ''))[:650]
        elif command == 'tool':
            try:
                if not self.awake or time.monotonic() < self.stopped_until:
                    raise ValueError('Session asleep or stopped')
                if self.tool_busy:
                    raise ValueError('Another action is awaiting approval')
                name = packet.get('tool')
                args = json.loads(packet.get('text', '{}'))
                if not isinstance(args, dict):
                    raise ValueError('Expected object arguments')
                if name == 'look_at_screen':
                    text, image = self.capture()
                    self.reply(packet, text, image)
                elif name == 'point_at_spot':
                    if not self.snapshot_at or time.monotonic() - self.snapshot_at > 30:
                        raise ValueError('Look at the screen first')
                    x, y = grid_point(args, *self.snapshot_size)
                    screen = QApplication.primaryScreen()
                    from PySide6.QtCore import QPoint
                    self.overlay.target = QPoint(round(x / screen.devicePixelRatio()), round(y / screen.devicePixelRatio()))
                    self.reply(packet, 'Pointing there')
                elif name in ('stop_pointing', 'go_to_sleep'):
                    self.overlay.target = None
                    self.reply(packet, 'Going home')
                elif name in ACTIONS:
                    self.tool_busy = True
                    try:
                        self.act(packet, name, args)
                    finally:
                        self.tool_busy = False
                else:
                    raise ValueError('Unsupported tool')
            except Exception as error:
                self.reply(packet, 'Unable to perform tool: ' + str(error))

    def token_ready(self, payload):
        generation, result = payload
        self.token_pending = False
        if generation == self.generation and self.connected:
            self.server.send(result)
        elif self.connected:
            self.server.send({'command': 'error', 'callID': result.get('callID'), 'text': 'Host settings changed. Start again.'})

    def reply(self, packet, text, image=None, command='toolResult'):
        result = {'command': command, 'callID': packet.get('callID'), 'text': text}
        if image:
            result['image'] = image
        self.server.send(result)

    def capture(self):
        self.overlay.hide()
        try:
            with mss.mss() as capture:
                monitor = next((m for m in capture.monitors[1:] if m['left'] == 0 and m['top'] == 0), capture.monitors[1])
                shot = capture.grab(monitor)
                image = Image.frombytes('RGB', shot.size, shot.rgb)
            self.snapshot_size = image.size
            self.snapshot_at = time.monotonic()
            image.thumbnail((1600, 1600))
            output = io.BytesIO()
            image.save(output, format='JPEG', quality=75)
            screen = QApplication.primaryScreen().geometry()
            mouse = QCursor.pos()
            x = round((mouse.x() - screen.left()) * 1000 / max(1, screen.width() - 1))
            y = round((mouse.y() - screen.top()) * 1000 / max(1, screen.height() - 1))
            return f'Primary display, 0-1000 coordinate grid. Mouse at ({x}, {y}). No OCR IDs available.', base64.b64encode(output.getvalue()).decode()
        finally:
            self.overlay.show()

    def act(self, packet, name, args):
        if not self.controls.isChecked() or not self.awake or not self.connected:
            raise ValueError('Computer control disabled')
        if not self.snapshot_at or time.monotonic() - self.snapshot_at > 30:
            raise ValueError('Take a fresh look at the screen before acting')
        validate(name, args, *self.snapshot_size)
        generation = self.generation
        foreground = ctypes.windll.user32.GetForegroundWindow() if sys.platform == 'win32' else None
        answer = QMessageBox.question(self, 'Allow Bluey to act?', name + '\n' + json.dumps(args, ensure_ascii=False)[:2200],
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes or generation != self.generation or not self.connected or not self.controls.isChecked() or not self.awake:
            raise ValueError('Action cancelled')
        if foreground:
            if not ctypes.windll.user32.SetForegroundWindow(foreground):
                raise ValueError('Cannot restore the target window')
            time.sleep(0.15)
            if ctypes.windll.user32.GetForegroundWindow() != foreground:
                raise ValueError('Target focus changed')
        import pyautogui as pg
        pg.FAILSAFE = True
        pg.PAUSE = 0.05
        if name == 'click':
            old = pg.position()
            try:
                pg.click(*grid_point(args, *self.snapshot_size), clicks=2 if args.get('double') else 1,
                         interval=0.1, button='right' if args.get('right') else 'left')
            finally:
                pg.moveTo(old)
        elif name == 'type_text':
            pg.write(args['text'], interval=0.003)
            if args.get('press_return'):
                pg.press('enter')
        elif name == 'press_keys':
            pg.hotkey(*args['keys'].split('+'))
        elif name == 'scroll':
            old = pg.position()
            try:
                pg.moveTo(self.snapshot_size[0] // 2, self.snapshot_size[1] // 2)
                pg.scroll(args.get('amount', 3) * (1 if args['direction'] == 'up' else -1))
            finally:
                pg.moveTo(old)
        elif name == 'drag':
            old = pg.position()
            try:
                pg.moveTo(*grid_point(args, *self.snapshot_size, 'from_'))
                pg.dragTo(*grid_point(args, *self.snapshot_size, 'to_'), duration=0.6)
            finally:
                pg.mouseUp()
                pg.moveTo(old)
        elif name == 'open_app':
            subprocess.Popen([APPS[args['name']]], shell=False)
        elif name == 'open_url':
            webbrowser.open(args['url'])
        text, image = self.capture()
        self.reply(packet, 'Action completed. ' + text, image)

    def closeEvent(self, event):
        self.timer.stop()
        self.server.close()
        self.overlay.close()
        event.accept()


def main():
    if sys.platform != 'win32' and os.getenv('BLUEY_TEST_UI') != '1':
        print('The desktop host requires Windows 10 or 11.')
        return 1
    if sys.platform == 'win32':
        from ctypes import wintypes
        ctypes.windll.user32.GetForegroundWindow.restype = wintypes.HWND
        ctypes.windll.user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        ctypes.windll.user32.SetForegroundWindow.restype = wintypes.BOOL
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
    app = QApplication(sys.argv)
    host = Host()
    host.show()
    return app.exec()
