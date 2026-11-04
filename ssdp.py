"""IPv4 SSDP multicast discovery on the selected LAN adapter."""
import os
import socket
import threading
import time
from email.utils import formatdate

SSDP_ADDR = ('239.255.255.250', 1900)
MAX_SSDP_PACKET = 4096
SERVER_STRING = 'Windows UPnP/1.0 LocalMediaRelay/0.1'
# WinSDK ws2def.h. This socket shares multicast and cannot receive unicast.
SO_REUSE_MULTICASTPORT = 0x3008


def lan_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(('192.0.2.1', 9))
            return sock.getsockname()[0]
        except OSError:
            return ''


class Discovery:
    def __init__(self, host, base, udn, boot_id):
        self.base, self.boot_id = base, boot_id
        self.targets = ssdp_targets(udn)
        self.stopped = threading.Event()
        self.receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            option = SO_REUSE_MULTICASTPORT if os.name == 'nt' else socket.SO_REUSEADDR
            self.receiver.setsockopt(socket.SOL_SOCKET, option, 1)
            self.receiver.bind(('', SSDP_ADDR[1]))
            interface = socket.inet_aton(host)
            membership = socket.inet_aton(SSDP_ADDR[0]) + interface
            self.receiver.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            self.receiver.settimeout(0.25)
            self.sender.bind((host, 0))
            self.sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, interface)
            self.sender.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            self.announce('ssdp:alive')
        except OSError as exc:
            self.receiver.close()
            self.sender.close()
            raise OSError(f'Could not start DLNA discovery on {host} (UDP 1900): {exc}') from exc
        self.error = None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def announce(self, kind):
        for target, usn in self.targets:
            self.sender.sendto(ssdp_notify(self.base, self.boot_id, target, usn, kind), SSDP_ADDR)

    def run(self):
        with self.receiver, self.sender:
            next_announcement = time.monotonic() + 1
            try:
                while not self.stopped.is_set():
                    now = time.monotonic()
                    if now >= next_announcement:
                        self.announce('ssdp:alive')
                        next_announcement = now + 30
                    try:
                        packet, client = self.receiver.recvfrom(65535)
                    except (TimeoutError, ConnectionResetError):
                        continue
                    if len(packet) > MAX_SSDP_PACKET or not packet.upper().startswith(b'M-SEARCH '):
                        continue
                    headers = parse_ssdp(packet)
                    if headers.get('MAN', '').strip('"').lower() != 'ssdp:discover':
                        continue
                    for target, usn in self.targets:
                        if headers.get('ST') in ('ssdp:all', target):
                            self.sender.sendto(ssdp_response(self.base, self.boot_id, target, usn), client)
                self.announce('ssdp:byebye')
            except OSError as exc:
                self.error = f'DLNA discovery stopped: {exc}'

    def stop(self):
        self.stopped.set()
        self.thread.join()


def ssdp_targets(udn) -> list[tuple[str, str]]:
    return [
        ('upnp:rootdevice', f'{udn}::upnp:rootdevice'),
        (udn, udn),
        ('urn:schemas-upnp-org:device:MediaServer:1', f'{udn}::urn:schemas-upnp-org:device:MediaServer:1'),
        ('urn:schemas-upnp-org:service:ContentDirectory:1', f'{udn}::urn:schemas-upnp-org:service:ContentDirectory:1'),
        ('urn:schemas-upnp-org:service:ConnectionManager:1', f'{udn}::urn:schemas-upnp-org:service:ConnectionManager:1'),
    ]


def ssdp_response(base, boot_id, st: str, usn: str) -> bytes:
    location = f'{base}/dlna/device.xml'
    lines = [
        'HTTP/1.1 200 OK',
        'CACHE-CONTROL: max-age=1800',
        f'DATE: {formatdate(usegmt=True)}',
        'EXT:',
        f'LOCATION: {location}',
        f'SERVER: {SERVER_STRING}',
        f'ST: {st}',
        f'USN: {usn}',
        f'BOOTID.UPNP.ORG: {boot_id}',
        'CONFIGID.UPNP.ORG: 1',
        '',
        '',
    ]
    return '\r\n'.join(lines).encode('utf-8')


def ssdp_notify(base, boot_id, nt: str, usn: str, nts: str) -> bytes:
    location = f'{base}/dlna/device.xml'
    lines = [
        'NOTIFY * HTTP/1.1',
        'HOST: 239.255.255.250:1900',
        'CACHE-CONTROL: max-age=1800',
        f'LOCATION: {location}',
        f'NT: {nt}',
        f'NTS: {nts}',
        f'SERVER: {SERVER_STRING}',
        f'USN: {usn}',
        f'BOOTID.UPNP.ORG: {boot_id}',
        'CONFIGID.UPNP.ORG: 1',
        '',
        '',
    ]
    return '\r\n'.join(lines).encode('utf-8')


def parse_ssdp(data: bytes) -> dict[str, str]:
    text = data.decode('utf-8', errors='ignore')
    headers = {}
    for line in text.splitlines()[1:]:
        if ':' in line:
            key, value = line.split(':', 1)
            headers[key.strip().upper()] = value.strip()
    return headers
