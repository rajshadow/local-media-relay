"""Exercise the owned server using synthetic clips and local HTTP clients."""
import argparse
import json
import socket
import stat
import struct
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

import settings
from dlna import token
from relay import Relay
from ssdp import SSDP_ADDR, lan_ip, parse_ssdp
from thumbnails import jpeg_preview

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


def sparse_fixtures(folder):
    fixtures = []
    for gigabytes in (50, 100):
        length = gigabytes * 10**9
        path = folder / f'sparse-{gigabytes}GB.mp4'
        path.touch()
        subprocess.run(['fsutil.exe', 'sparse', 'setflag', str(path)], check=True, capture_output=True)
        if not path.stat().st_file_attributes & stat.FILE_ATTRIBUTE_SPARSE_FILE:
            raise RuntimeError(f'Sparse flag missing for {path.name}')
        markers = {
            0: b'start',
            4 * 2**30 + 123: b'beyond-4-GiB',
            length - 3: b'end',
        }
        with path.open('r+b') as file:
            for offset, marker in markers.items():
                file.seek(offset)
                file.write(marker)
        fixtures.append((path.name, length, markers))
    return fixtures


def check_large_files(base, fixtures):
    for name, length, markers in fixtures:
        url = base + '/media/2/' + name
        status, headers, _ = request(url, method='HEAD')
        assert status == 200 and int(headers['Content-Length']) == length
        for offset, marker in markers.items():
            end = offset + len(marker) - 1
            query = Request(url, headers={'Range': f'bytes={offset}-{end}'})
            with CLIENT.open(query, timeout=3) as response:
                assert response.status == 206
                assert response.headers['Content-Range'] == f'bytes {offset}-{end}/{length}'
                assert response.read(len(marker) + 1) == marker
        print(f'{name}: HEAD and byte ranges at start, beyond 4 GiB, and EOF passed.')


def check_preview(relay, key):
    url = relay.base + '/dlna/thumb/' + token(key) + '.jpg'
    if not relay.thumbnails.ffmpeg:
        print('Preview generation skipped because ffmpeg is not on PATH.')
        return
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            status, headers, image = request(url)
        except HTTPError as error:
            assert error.code == 404
            time.sleep(0.1)
            continue
        assert status == 200 and headers['Content-Type'] == 'image/jpeg'
        assert image.startswith(b'\xff\xd8') and image.endswith(b'\xff\xd9')
        generated = relay.thumbnails.cache_path(relay.library.path(key))
        assert jpeg_preview(generated)
        before = generated.stat().st_mtime_ns
        assert request(url)[2] == image
        assert generated.stat().st_mtime_ns == before
        return
    raise AssertionError('Thumbnail generation timed out')


def check_restricted(folders, host):
    approved = Relay(folders, host, 'uuid:' + str(uuid.uuid4()), allowed_clients=[host])
    try:
        find_relay(approved, host, approved.udn)
        assert request(approved.base + '/dlna/device.xml')[0] == 200
        assert request(approved.base + '/media/0/Color%20bars.mp4')[0] == 200
    finally:
        approved.stop()
    denied = Relay(folders, host, 'uuid:' + str(uuid.uuid4()), allowed_clients=['192.0.2.123'])
    try:
        for path in ('/dlna/device.xml', '/media/0/Color%20bars.mp4',
                     '/dlna/thumb/' + token('0/Color bars.mp4') + '.jpg'):
            try:
                request(denied.base + path, headers={'X-Forwarded-For': '192.0.2.123',
                                                    'X-Relay-Client': '192.0.2.123'})
            except HTTPError as error:
                assert error.code == 403
            else:
                raise AssertionError('Denied client accessed a protected route')
        try:
            find_relay(denied, host, denied.udn)
        except AssertionError as error:
            assert str(error).startswith('No SSDP response')
        else:
            raise AssertionError('Discovery answered a denied client')
    finally:
        denied.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--large-files', action='store_true', help='Check sparse 50 GB and 100 GB byte offsets')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='relay-smoke-') as temp:
        settings.APP_DIR = Path(temp) / 'state'
        settings.LOG_DIR = settings.APP_DIR / 'logs'
        folder = Path(temp) / 'Tést {folder}'
        folder.mkdir()
        clip = folder / '100%20 # café {x}_ALPHA.mp4'
        clip.write_bytes(CLIP.read_bytes())
        photo = folder / 'photo.png'
        def png_chunk(kind, data):
            checksum = zlib.crc32(kind + data)
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', checksum)

        header = struct.pack('>IIBBBBB', 2, 2, 8, 6, 0, 0, 0)
        pixels = (b'\0' + b'\xff\0\0\xff' * 2) * 2
        photo.write_bytes(b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', header)
                          + png_chunk(b'IDAT', zlib.compress(pixels)) + png_chunk(b'IEND', b''))
        (folder / 'private.txt').write_text('must not be served', encoding='utf-8')
        large_files = sparse_fixtures(folder) if args.large_files else ()
        run([*FOLDERS, folder], clip, large_files)


def run(folders, clip, large_files):
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
            check_large_files(base, large_files)
            media = base + '/media/0/Color%20bars.mp4'
            assert request(media)[2] == original
            photo = relay.library.path('2/photo.png')
            assert request(base + '/media/2/photo.png')[2] == photo.read_bytes()
            assert request(base + '/media/2/photo.png')[1]['Content-Type'] == 'image/png'
            check_preview(relay, '0/Color bars.mp4')
            check_preview(relay, '2/photo.png')
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
            bookmark_id = token('0/Color bars.mp4')
            bookmark_headers = {'SOAPAction': '"urn:schemas-upnp-org:service:ContentDirectory:1#X_SetBookmark"',
                                'X-Relay-Client': 'spoofed'}
            bookmark = (f'<Action><ObjectID>{bookmark_id}</ObjectID>'
                        '<PosSecond>91500</PosSecond></Action>').encode()
            assert request(base + '/dlna/content-directory/control', data=bookmark, headers=bookmark_headers)[0] == 200
            browse = (f'<Browse><ObjectID>{bookmark_id}</ObjectID>'
                      '<BrowseFlag>BrowseMetadata</BrowseFlag></Browse>').encode()
            metadata = ET.fromstring(request(base + '/dlna/content-directory/control',
                                              data=browse, headers=soap)[2]).findtext('.//Result')
            assert 'BM=91500' in metadata
            assert relay.checkpoints.get(CLIP, host) == 91.5
            assert relay.checkpoints.get(CLIP, 'spoofed') == 0
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
    check_restricted(folders, host)
    print('Smoke passed: discovery, images, previews, ranges, checkpoint identity, access controls and lifecycle.')


if __name__ == '__main__':
    main()
