"""One authorized phone per host. TLS and random pairing key are both required."""
import base64
import datetime as dt
import ipaddress
import json
import os
import queue
from pathlib import Path
import secrets
import socket
import ssl
import threading
import urllib.request

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .protocol import PORT, authenticated, encode, read_packet, session


def identity(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    certificate, key = directory / 'host.crt', directory / 'host.key'
    if not certificate.exists() or not key.exists():
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Bluey Windows')])
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(private.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
                .not_valid_after(now + dt.timedelta(days=3650))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName('bluey.local')]), critical=False)
                .sign(private, hashes.SHA256()))
        key.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
        os.chmod(key, 0o600)
        certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    cert = x509.load_pem_x509_certificate(certificate.read_bytes())
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate, key)
    return context, fingerprint


def local_addresses():
    addresses = set()
    for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        address = info[4][0]
        if not ipaddress.ip_address(address).is_loopback:
            addresses.add(address)
    return sorted(addresses)


def mint_token(api_key, model, controls):
    if not api_key:
        raise ValueError('Set your OpenAI API key on Windows first.')
    body = json.dumps({'expires_after': {'anchor': 'created_at', 'seconds': 600},
                       'session': session(model, controls)}).encode()
    request = urllib.request.Request('https://api.openai.com/v1/realtime/client_secrets', body,
                                     {'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json'})
    # Do not surface raw API errors, which can contain private request details.
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            token = json.load(response)['value']
    except Exception as error:
        raise ValueError('OpenAI could not start a session. Check API key, model access, billing, and internet.') from error
    return token


class PhoneServer:
    def __init__(self, directory, on_packet, on_status, port=PORT):
        self.context, self.fingerprint = identity(directory)
        self.pairing_key = secrets.token_hex(16)  # invalidates previously shared codes on each launch
        self.on_packet, self.on_status = on_packet, on_status
        self.port = port
        self.connection = None
        self.listener = None
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.listener_thread = None
        self.outgoing = queue.Queue(maxsize=32)

    def pairing_code(self, address):
        data = {'version': 1, 'host': address, 'port': self.port,
                'fingerprint': self.fingerprint, 'key': self.pairing_key}
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip('=')

    def start(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('0.0.0.0', self.port))
        listener.listen(4)
        listener.settimeout(1)
        self.port = listener.getsockname()[1]
        self.listener = listener
        self.listener_thread = threading.Thread(target=self._listen, daemon=True)
        self.listener_thread.start()
        threading.Thread(target=self._send_loop, daemon=True).start()

    def _listen(self):
        while not self.closed.is_set():
            try:
                raw, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            # Bounded concurrency: one handshake at a time, limited to five seconds.
            try:
                raw.settimeout(5)
                connection = self.context.wrap_socket(raw, server_side=True)
                stream = connection.makefile('rb')
                try:
                    if not authenticated(read_packet(stream), self.pairing_key):
                        connection.sendall(encode({'command': 'error', 'text': 'Pairing rejected'}))
                        raise ValueError('Pairing rejected')
                    with self.lock:
                        if self.connection is not None or self.closed.is_set():
                            raise ValueError('Host already connected')
                        connection.sendall(encode({'command': 'paired', 'hello': socket.gethostname()}))
                        self.connection = connection
                    self.on_status('Android connected')
                    connection.settimeout(90)
                    threading.Thread(target=self._receive, args=(connection, stream), daemon=True).start()
                except Exception:
                    stream.close()
                    connection.close()
                    with self.lock:
                        if self.connection is connection:
                            self.connection = None
                    raise
            except (OSError, ValueError, EOFError, ssl.SSLError):
                raw.close()

    def _receive(self, connection, stream):
        try:
            while not self.closed.is_set():
                packet = read_packet(stream)
                if packet.get('command') == 'ping':
                    self.send({'command': 'pong'})
                else:
                    self.on_packet(packet)
        except (OSError, ValueError, EOFError):
            pass
        finally:
            stream.close()
            connection.close()
            with self.lock:
                if self.connection is connection:
                    self.connection = None
            self.on_status('Phone disconnected')

    def send(self, packet):
        data = encode(packet)
        with self.lock:
            connection = self.connection
        if connection:
            try:
                self.outgoing.put_nowait((connection, data))
            except queue.Full:
                # Face updates are expendable. Close a stalled peer rather than lose a tool reply.
                if 'face' not in packet:
                    try:
                        connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    connection.close()

    def _send_loop(self):
        while not self.closed.is_set():
            try:
                connection, data = self.outgoing.get(timeout=0.5)
            except queue.Empty:
                continue
            with self.lock:
                current = self.connection
            if current is not connection:
                continue
            try:
                connection.sendall(data)
            except OSError:
                connection.close()

    def close(self):
        self.closed.set()
        if self.listener:
            self.listener.close()
        with self.lock:
            if self.connection:
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.connection.close()
                self.connection = None
