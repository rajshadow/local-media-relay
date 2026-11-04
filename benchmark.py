"""Compare the former Python media handler and Caddy over Windows loopback."""
import argparse
import concurrent.futures
import os
import random
import re
import shutil
import socket
import statistics
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import dlna
from library import Library, VIDEO_EXTS
from urllib.parse import unquote, urlsplit
from relay import Handler, HttpServer

HOST = '127.0.0.1'
MIB = 1024 ** 2
SIZE = 512 * MIB
RANGE_SIZE = 8 * MIB


class PythonMediaHandler(Handler):
    def do_GET(self):
        key = unquote(urlsplit(self.path).path).removeprefix('/media/')
        self.media(self.server.relay.library.path(key))

    def media(self, path):
        if path is None or path.suffix.lower() not in VIDEO_EXTS or not path.is_file():
            self.send_error(404)
            return
        with path.open('rb') as file:
            size = file.seek(0, 2)
            start, end = 0, size - 1
            requested = self.headers.get('Range')
            match = re.fullmatch(r'bytes=([0-9]{0,20})-([0-9]{0,20})', requested or '')
            if match:
                first, last = match.groups()
                if first:
                    start = int(first)
                    if last:
                        end = min(end, int(last))
                elif last and int(last):
                    start = max(0, size - int(last))
                else:
                    start = size
                if start > end:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
            self.send_response(206 if match else 200)
            self.send_header('Content-Type', dlna.mime_for(path))
            self.send_header('Content-Length', str(end - start + 1))
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('contentFeatures.dlna.org', dlna.DLNA_FEATURES)
            self.send_header('transferMode.dlna.org', 'Streaming')
            if match:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.end_headers()
            if self.command == 'HEAD':
                return
            file.seek(start)
            remaining = end - start + 1
            while remaining and not self.server.relay.stopped.is_set():
                chunk = file.read(min(remaining, 256 * 1024))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def transfer(url, offset=None):
    command = ['curl.exe', '--noproxy', '*', '--http1.1', '--silent', '--show-error',
               '--fail', '--max-time', '90', '--output', 'NUL',
               '--write-out', '%{http_code} %{size_download} %{time_starttransfer} %{time_total}']
    if offset is not None:
        command += ['--range', f'{offset}-{offset + RANGE_SIZE - 1}']
    result = subprocess.run([*command, url], check=True, capture_output=True, text=True)
    status, size, first_byte, elapsed = result.stdout.split()
    expected = ('200', SIZE) if offset is None else ('206', RANGE_SIZE)
    if (status, int(size)) != expected:
        raise RuntimeError(f'Unexpected HTTP result: {result.stdout}')
    return float(first_byte), float(elapsed)


@contextmanager
def servers(folder, caddy_exe):
    http = HttpServer((HOST, 0), PythonMediaHandler)
    http.relay = SimpleNamespace(library=Library([folder]), stopped=threading.Event())
    worker = threading.Thread(target=http.serve_forever, args=(0.1,), daemon=True)
    worker.start()
    try:
        with socket.socket() as reservation:
            reservation.bind((HOST, 0))
            port = reservation.getsockname()[1]
        config = folder / 'Caddyfile'
        config.write_text(
            '{\n  admin off\n  auto_https off\n  persist_config off\n}\n'
            f'http://{HOST}:{port} {{\n'
            '  @clip path /payload.mp4\n'
            f'  root "{folder.as_posix()}"\n'
            '  file_server @clip\n}\n', encoding='utf-8')
        with (folder / 'caddy.log').open('w+b') as log:
            caddy = subprocess.Popen([str(caddy_exe), 'run', '--config', str(config),
                                      '--adapter', 'caddyfile'], cwd=folder,
                                     stdout=log, stderr=log)
            try:
                for _ in range(100):
                    if caddy.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(log.read().decode(errors='replace'))
                    try:
                        with socket.create_connection((HOST, port), timeout=0.1):
                            break
                    except OSError:
                        time.sleep(0.05)
                else:
                    raise RuntimeError('Caddy startup timed out')
                yield {'Python': f'http://{HOST}:{http.server_port}/media/0/payload.mp4',
                       'Caddy': f'http://{HOST}:{port}/payload.mp4'}
            finally:
                if caddy.poll() is None:
                    caddy.terminate()
                    caddy.wait(timeout=10)
    finally:
        http.relay.stopped.set()
        http.shutdown()
        http.server_close()
        worker.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--caddy', required=True, type=Path, help='Native Caddy executable, without a launcher shim')
    args = parser.parse_args()
    caddy_exe = args.caddy.resolve(strict=True)
    if not caddy_exe.is_file() or not shutil.which('curl.exe'):
        raise SystemExit('A native Caddy executable and curl.exe are required.')
    with tempfile.TemporaryDirectory(prefix='relay-bench-') as directory:
        folder = Path(directory)
        rng = random.Random(7)
        with (folder / 'payload.mp4').open('wb') as payload:
            for _ in range(SIZE // MIB):
                payload.write(rng.randbytes(MIB))
        offsets = [rng.randrange(SIZE - RANGE_SIZE) for _ in range(30)]
        with servers(folder, caddy_exe) as urls, concurrent.futures.ThreadPoolExecutor(4) as pool:
            samples = {name: {'single': [], 'four': [], 'range': []} for name in urls}
            for url in urls.values():
                transfer(url)
            order = list(urls)
            for round_number in range(5):
                for name in order[::(-1 if round_number % 2 else 1)]:
                    _, elapsed = transfer(urls[name])
                    samples[name]['single'].append(SIZE / MIB / elapsed)
                    start = time.perf_counter()
                    list(pool.map(transfer, [urls[name]] * 4))
                    samples[name]['four'].append(4 * SIZE / MIB / (time.perf_counter() - start))
            for index, offset in enumerate(offsets):
                for name in order[::(-1 if index % 2 else 1)]:
                    samples[name]['range'].append(transfer(urls[name], offset))
            for name, data in samples.items():
                first, elapsed = zip(*data['range'])
                p95 = sorted(first)[28]
                print(f'{name}: single {statistics.median(data["single"]):.0f} MiB/s '
                      f'({min(data["single"]):.0f}-{max(data["single"]):.0f}); '
                      f'four aggregate {statistics.median(data["four"]):.0f} MiB/s '
                      f'({min(data["four"]):.0f}-{max(data["four"]):.0f}); '
                      f'range first byte p50/p95 {statistics.median(first)*1000:.2f}/{p95*1000:.2f} ms; '
                      f'range complete p50 {statistics.median(elapsed)*1000:.2f} ms', flush=True)


if __name__ == '__main__':
    main()
