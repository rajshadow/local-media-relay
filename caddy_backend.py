"""Start a private Caddy instance and own its native process handle."""
import os
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

import settings
from access import allowed_addresses
from dlna import DLNA_FEATURES
from windows_job import ChildJob


def caddy_executable():
    local = Path(__file__).with_name('tools') / 'caddy.exe'
    if local.is_file():
        return local
    chocolatey = Path(os.environ.get('ChocolateyInstall', r'C:\ProgramData\chocolatey'))
    native = chocolatey / 'lib' / 'caddy' / 'tools' / 'caddy.exe'
    if native.is_file():
        return native
    found = shutil.which('caddy.exe')
    if found:
        executable = Path(found).resolve()
        if executable.parent != chocolatey.resolve() / 'bin':
            return executable
    raise FileNotFoundError('Caddy was not found. Put the native caddy.exe in tools or on PATH.')


class Caddy:
    def __init__(self, host, backend_port, udn, allowed_clients=None):
        self.allowed_clients = allowed_addresses(allowed_clients)
        self.health_token = uuid.uuid4().hex
        executable = caddy_executable()
        with socket.socket() as reservation:
            reservation.bind((host, 0))
            port = reservation.getsockname()[1]
        self.base = f'http://{host}:{port}'
        self.runtime = tempfile.TemporaryDirectory(prefix='local-media-relay-')
        self.process = None
        self.job = None
        self.job_attached = False
        try:
            folder = Path(self.runtime.name)
            config = folder / 'Caddyfile'
            config.write_text(self.config(host, port, backend_port, self.allowed_clients, self.health_token), encoding='utf-8')
            settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            self.log_path = settings.LOG_DIR / f'{timestamp}_{uuid.uuid4().hex[:12]}.jsonl'
            env = dict(os.environ, XDG_CONFIG_HOME=str(folder), XDG_DATA_HOME=str(folder))
            self.job = ChildJob()
            with self.log_path.open('xb') as output:
                self.process = subprocess.Popen(
                    [str(executable), 'run', '--config', str(config), '--adapter', 'caddyfile'],
                    cwd=folder, env=env, stdout=output, stderr=output,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.job.attach(self.process)
            self.job_attached = True
            self.wait_until_ready()
        except BaseException:
            self.stop()
            raise

    def wait_until_ready(self):
        client = build_opener(ProxyHandler({}))
        for _ in range(60):
            if self.process.poll() is not None:
                break
            try:
                with client.open(self.base + '/_relay-health/' + self.health_token, timeout=0.25) as response:
                    if response.read() == self.health_token.encode():
                        return
            except (URLError, TimeoutError):
                pass
            time.sleep(0.05)
        tail = self.log_path.read_text(encoding='utf-8', errors='replace').splitlines()[-2:]
        raise OSError(f'Caddy did not start. Log: {self.log_path}\n' + ' '.join(tail))

    def stop(self):
        if self.job is not None:
            self.job.close()
        if self.process is not None and self.process.poll() is None:
            if not self.job_attached:
                self.process.terminate()
            self.process.wait(timeout=5)
        self.runtime.cleanup()

    @staticmethod
    def config(host, port, backend_port, allowed_clients=None, health_token=None):
        backend = f'127.0.0.1:{backend_port}'
        addresses = allowed_addresses(allowed_clients)
        access_gate = ''
        if addresses is not None:
            access_gate = f'''        @blocked not remote_ip {" ".join(addresses)}
        respond @blocked 403
'''
        health_route = ''
        if health_token is not None:
            health_route = f'''        @health {{
            method GET HEAD
            path /_relay-health/{health_token}
            remote_ip {host}
        }}
        handle @health {{
            respond "{health_token}" 200
        }}
'''
        return f'''{{
    admin off
    auto_https off
    persist_config off
}}
http://{host}:{port} {{
    bind {host}
    log {{
        output stderr
        format json
    }}
    route {{
{health_route}{access_gate}        @media {{
            method GET HEAD
            path /media/*
        }}
        handle @media {{
            route {{
                forward_auth {backend} {{
                    uri /internal/media
                    copy_headers X-Relay-Root X-Relay-File X-Relay-Type
                }}
                rewrite {{http.request.header.X-Relay-File}}
                header {{
                    Content-Type {{http.request.header.X-Relay-Type}}
                    contentFeatures.dlna.org {DLNA_FEATURES}
                    transferMode.dlna.org Streaming
                }}
                file_server {{
                    root {{http.request.header.X-Relay-Root}}
                    disable_canonical_uris
                }}
            }}
        }}
        handle /dlna/* {{
            reverse_proxy {backend} {{
                header_up X-Relay-Client {{remote_host}}
            }}
        }}
        handle {{
            respond 404
        }}
    }}
}}
'''
