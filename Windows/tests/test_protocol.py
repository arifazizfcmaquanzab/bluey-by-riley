import io
import json
import math
import socket
import ssl
import tempfile
import time
import unittest
from bluey.protocol import MAX_PACKET, authenticated, coordinate, encode, grid_point, read_packet, session
from bluey.actions import validate
from bluey.server import PhoneServer, identity


class ProtocolTests(unittest.TestCase):
    def test_roundtrip(self):
        packet = {'command': 'caption', 'text': 'Hello\nBluey, سلام'}
        self.assertEqual(read_packet(io.BytesIO(encode(packet))), packet)

    def test_bounded_frames(self):
        for data in [b'{}', b'[]\n', b'nope\n', b'x' * (MAX_PACKET + 1)]:
            with self.assertRaises(ValueError):
                read_packet(io.BytesIO(data))
        with self.assertRaises(EOFError):
            read_packet(io.BytesIO())

    def test_auth_required(self):
        self.assertTrue(authenticated({'command': 'pair', 'pairingKey': 'secret'}, 'secret'))
        for packet in [{}, {'command': 'tool', 'pairingKey': 'secret'}, {'command': 'pair', 'pairingKey': 'wrong'}, {'command': 'pair', 'pairingKey': 1}]:
            self.assertFalse(authenticated(packet, 'secret'))

    def test_invalid_coordinates(self):
        for value in [-1, 1001, float('inf'), float('nan'), True, '1', None]:
            with self.assertRaises(ValueError):
                coordinate(value)
        self.assertEqual(grid_point({'x': 1000, 'y': 1000}, 1920, 1080), (1919, 1079))

    def test_controls_excluded_until_enabled(self):
        names = lambda enabled: {t['name'] for t in session('gpt-realtime-2.1', enabled)['tools']}
        self.assertNotIn('click', names(False))
        self.assertIn('click', names(True))
        self.assertIsNone(session('x', False)['audio']['input']['turn_detection'])

    def test_actions_reject_injection_and_destructive_shortcuts(self):
        cases = [('open_app', {'name': 'cmd.exe /c del *'}), ('open_url', {'url': 'file:///C:/Windows'}),
                 ('open_url', {'url': 'javascript:alert(1)'}), ('open_url', {'url': 'https://a:b@example.com'}),
                 ('open_url', {'url': 'http://127.0.0.1/'}), ('press_keys', {'keys': 'win+l'}),
                 ('press_keys', {'keys': 'alt+f4'}), ('type_text', {'text': 'x\ny'}),
                 ('click', {'x': 2, 'y': 2, 'right': 'false'}), ('scroll', {'direction': 'up', 'amount': True}),
                 ('scroll', {'direction': 'up', 'amount': 999})]
        for name, args in cases:
            with self.subTest(name=name, args=args), self.assertRaises(ValueError):
                validate(name, args, 1920, 1080)
        validate('click', {'x': 10, 'y': 20}, 1920, 1080)
        validate('open_url', {'url': 'https://example.com'}, 1920, 1080)


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.received = []
        self.server = PhoneServer(self.directory.name, self.received.append, lambda _: None, port=0)
        self.server.start()

    def tearDown(self):
        self.server.close()
        self.directory.cleanup()

    def connect(self):
        # Tests connect using the exact persisted host certificate as trust anchor.
        context = ssl.create_default_context(cafile=self.directory.name + '/host.crt')
        connection = context.wrap_socket(socket.create_connection(('127.0.0.1', self.server.port), timeout=2), server_hostname='bluey.local')
        return connection, connection.makefile('rb')

    def test_tls_identity_persists(self):
        _, fingerprint = identity(self.directory.name)
        self.assertEqual(fingerprint, self.server.fingerprint)

    def test_unpaired_cannot_dispatch_tools(self):
        connection, stream = self.connect()
        connection.sendall(encode({'command': 'tool', 'tool': 'click'}))
        self.assertEqual(read_packet(stream)['command'], 'error')
        self.assertEqual(self.received, [])
        stream.close(); connection.close()

    def test_paired_phone_and_heartbeat(self):
        connection, stream = self.connect()
        connection.sendall(encode({'command': 'pair', 'pairingKey': self.server.pairing_key}))
        self.assertEqual(read_packet(stream)['command'], 'paired')
        connection.sendall(encode({'command': 'ping'}))
        self.assertEqual(read_packet(stream)['command'], 'pong')
        connection.sendall(encode({'command': 'caption', 'text': 'hi'}))
        for _ in range(40):
            if self.received: break
            time.sleep(0.01)
        self.assertEqual(self.received[0]['text'], 'hi')
        stream.close(); connection.close()

    def test_wrong_certificate_rejected(self):
        with tempfile.TemporaryDirectory() as other:
            identity(other)
            context = ssl.create_default_context(cafile=other + '/host.crt')
            raw = socket.create_connection(('127.0.0.1', self.server.port), timeout=2)
            with self.assertRaises(ssl.SSLCertVerificationError):
                context.wrap_socket(raw, server_hostname='bluey.local')
            raw.close()


if __name__ == '__main__': unittest.main()
