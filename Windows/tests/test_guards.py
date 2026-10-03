import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import time
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QMessageBox
from bluey.app import Host


class FakeServer:
    def __init__(self, *args): self.port = 0; self.sent = []
    def start(self): pass
    def pairing_code(self, _): return 'test-code'
    def send(self, packet): self.sent.append(packet)
    def close(self): pass


class ActionGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch('bluey.app.PhoneServer', FakeServer): self.host = Host()
        self.host.timer.stop()
        self.host.connected = True
        self.host.awake = True
        self.host.snapshot_size = (1920, 1080)
        self.host.snapshot_at = time.monotonic()
        self.host.controls.setChecked(True)
        self.host.snapshot_at = time.monotonic()
        self.packet = {'command': 'tool', 'tool': 'click', 'text': '{"x": 50, "y": 50}', 'callID': '1'}

    def tearDown(self):
        self.host.close()
        self.app.processEvents()

    def refusal(self, fragment):
        self.host.receive(self.packet)
        self.assertIn(fragment, self.host.server.sent[-1]['text'])

    def test_disabled(self):
        self.host.controls.setChecked(False)
        self.refusal('disabled')

    def test_stale_screen(self):
        self.host.snapshot_at = time.monotonic() - 31
        self.refusal('fresh look')

    def test_stop_invalidates(self):
        self.host.stop()
        self.refusal('asleep or stopped')

    def test_disconnect_blocks(self):
        self.host.status_changed('Phone disconnected')
        self.refusal('asleep or stopped')

    def test_reentrant_action_is_refused(self):
        self.host.tool_busy = True
        self.refusal('awaiting approval')

    def test_stop_during_approval_cancels_action(self):
        def approve(*args):
            self.host.stop()
            return QMessageBox.StandardButton.Yes
        with patch('bluey.app.sys.platform', 'linux'), patch.object(QMessageBox, 'question', side_effect=approve):
            self.refusal('cancelled')

    def test_sleep_and_rewake_during_approval_cancels_action(self):
        def approve(*args):
            self.host.receive({'command': 'asleep'})
            self.host.receive({'command': 'awake'})
            return QMessageBox.StandardButton.Yes
        with patch('bluey.app.sys.platform', 'linux'), patch.object(QMessageBox, 'question', side_effect=approve):
            self.refusal('cancelled')
