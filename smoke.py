"""Exercise the owned server using synthetic clips and local HTTP clients."""
import json
import socket
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, build_opener

import settings
from dlna import token
from relay import Relay
from ssdp import SSDP_ADDR, lan_ip, parse_ssdp

HERE = Path(__file__).resolve().parent
FOLDERS = [HERE / 'fixtures' / name for name in ('Color bars', 'Motion')]
CLIP = FOLDERS[0] / 'Color bars.mp4'
CLIENT = build_opener(ProxyHandler({}))


def request(url, **kwargs):
    with CLIENT.open(Request(url, **kwargs), timeout=3) as response:
        return response.status, response.headers, response.read()


def find_relay(relay, host, udn):
    search = ('M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n'
              'MAN: "ssdp:discover"\r\nMX: 1\r\nST: upnp:rootdevice\r\n\r\n').encode()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.bind((host, 0))
        client.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(host))
        client.settimeout(0.5)
        for _ in range(3):
            client.sendto(search, SSDP_ADDR)
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                try:
                    packet, sender = client.recvfrom(4096)
                except TimeoutError:
                    break
                headers = parse_ssdp(packet)
                if headers.get('USN') == udn + '::upnp:rootdevice':
                    assert sender[0] == host
                    assert headers['LOCATION'] == relay.base + '/dlna/device.xml'
                    return
    raise AssertionError('No SSDP response for the relay on the selected interface')


def main():
    with tempfile.TemporaryDirectory(prefix='relay-smoke-') as temp:
        settings.LOG_DIR = Path(temp) / 'logs'
        folder = Path(temp) / 'Tést {folder}'
        folder.mkdir()
        clip = folder / '100%20 # café {x}_ALPHA.mp4'
        clip.write_bytes(CLIP.read_bytes() + b'separate fixture')
        (folder / 'private.txt').write_text('must not be served', encoding='utf-8')
        run([*FOLDERS, folder], clip)


def run(folders, clip):
    original = CLIP.read_bytes()
    udn = 'uuid:' + str(uuid.uuid4())
    host = lan_ip()
    relay = Relay(folders, host, udn)
    try:
        second = Relay(folders, host, 'uuid:' + str(uuid.uuid4()))
        try:
            find_relay(relay, host, udn)
            base = relay.base
            assert second.base != base
            media = base + '/media/0/Color%20bars.mp4'
            assert request(media)[2] == original
            tagged_media = base + '/media/2/' + quote(clip.name, safe='')
            _, caddy_headers, received = request(tagged_media, headers={
                'X-Relay-Root': 'C:/', 'X-Relay-File': '/private.txt',
                'X-Forwarded-Uri': '/media/2/private.txt',
            })
            assert received == clip.read_bytes()
            assert caddy_headers['Content-Type'] == 'video/mp4'
            assert caddy_headers['contentFeatures.dlna.org']
            etag = caddy_headers['ETag']
            assert etag and request(tagged_media, headers={'Range': 'bytes=0-9', 'If-Range': etag})[0] == 206
            assert request(tagged_media, headers={'Range': 'bytes=0-9', 'If-Range': '"old"'})[2] == received
            assert int(request(media, method='HEAD')[1]['Content-Length']) == len(original)
            status, headers, data = request(media, headers={'Range': 'bytes=10-29'})
            assert (status, headers['Content-Range'], data) == (
                206, f'bytes 10-29/{len(original)}', original[10:30])
            assert request(media, headers={'Range': 'bytes=-20'})[2] == original[-20:]
            for url, headers, expected in (
                (media, {'Range': 'bytes=9999999-'}, 416),
                (base + '/browse', {}, 404),
                (base + '/deovr', {}, 404),
                (base + '/internal/media', {'X-Forwarded-Uri': '/media/0/Color%20bars.mp4'}, 404),
                (base + '/media/2/private.txt', {'X-Forwarded-Uri': '/media/0/Color%20bars.mp4'}, 404),
                (base + '/media/0/%2e%2e/Motion/Moving%20pattern.mp4', {}, 404),
            ):
                try:
                    request(url, headers=headers)
                except HTTPError as exc:
                    assert exc.code == expected
                else:
                    raise AssertionError(f'Expected HTTP {expected}')
            device = ET.fromstring(request(base + '/dlna/device.xml')[2])
            assert device.findtext('.//{*}UDN') == udn
            soap = {'SOAPAction': '"urn:schemas-upnp-org:service:ContentDirectory:1#Browse"'}
            for key, expected in (('', 'Motion'), ('0', media)):
                body = (f'<Browse><ObjectID>{token(key)}</ObjectID>'
                        '<BrowseFlag>BrowseDirectChildren</BrowseFlag></Browse>').encode()
                result = ET.fromstring(request(
                    base + '/dlna/content-directory/control', data=body, headers=soap)[2])
                assert expected in result.findtext('.//Result')
            relay.stop()
            assert relay.caddy.process.poll() is not None
            log = relay.caddy.log_path
            assert log != second.caddy.log_path
            events = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
            assert any(
                event.get('status') == 206
                and event['request']['uri'] == '/media/0/Color%20bars.mp4'
                and event['request']['headers'].get('Range') == ['bytes=10-29']
                and event['resp_headers'].get('Content-Range') == [f'bytes 10-29/{len(original)}']
                and event['size'] == 20
                and event['duration'] >= 0
                for event in events
            ), 'Missing range diagnostics in retained log'
            assert second.caddy.process.poll() is None
            try:
                request(base + '/dlna/device.xml')
            except URLError:
                pass
            else:
                raise AssertionError('Stopped relay still accepts connections')
            assert request(second.base + '/dlna/device.xml')[0] == 200
            find_relay(second, host, second.udn)
        finally:
            second.stop()
    finally:
        if not relay.stopped.is_set():
            relay.stop()
    restarted = Relay(folders, host, udn)
    try:
        find_relay(restarted, host, udn)
    finally:
        restarted.stop()
    assert CLIP.read_bytes() == original
    print('Smoke passed: SSDP, SOAP, Caddy range and If-Range, filename fidelity, file gating, owned restart, and persistent seek logs.')


if __name__ == '__main__':
    main()
