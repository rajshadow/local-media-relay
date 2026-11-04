"""DLNA HTTP transport and owned session lifecycle."""
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote, urlsplit

import dlna
from caddy_backend import Caddy
from library import Library, VIDEO_EXTS
from ssdp import Discovery


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(3)
        super().setup()

    def log_message(self, format, *args):
        pass

    def reply(self, body, content_type, status=200):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        relay = self.server.relay
        path = unquote(urlsplit(self.path).path)
        try:
            if path == '/internal/media':
                self.authorize_media(relay.library)
            elif path == '/dlna/device.xml':
                self.reply(dlna.device_xml(relay.udn), 'text/xml')
            elif path == '/dlna/content-directory.xml':
                self.reply(dlna.CONTENT_DIRECTORY_SCPD, 'text/xml')
            elif path == '/dlna/connection-manager.xml':
                self.reply(dlna.CONNECTION_MANAGER_SCPD, 'text/xml')
            else:
                self.send_error(404)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except (ValueError, OSError):
            self.send_error(404)

    def authorize_media(self, library):
        url = self.headers.get('X-Forwarded-Uri', '')
        path = unquote(urlsplit(url).path, errors='strict')
        if not path.startswith('/media/'):
            self.send_error(404)
            return
        file = library.path(path.removeprefix('/media/'))
        if file is None or file.suffix.lower() not in VIDEO_EXTS or not file.is_file():
            self.send_error(404)
            return
        self.send_response(204)
        self.send_header('X-Relay-Root', file.parent.as_posix().encode('utf-8').decode('latin-1'))
        self.send_header('X-Relay-File', '/' + quote(file.name, safe=''))
        self.send_header('X-Relay-Type', dlna.mime_for(file))
        self.end_headers()

    def do_POST(self):
        path = urlsplit(self.path).path
        action = self.headers.get('SOAPAction', '').strip('"').rsplit('#', 1)[-1]
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536:
                self.send_error(400)
                return
            body = self.rfile.read(length)
            if path == '/dlna/content-directory/control':
                status, response = self.server.relay.content.content_action(action, body)
            elif path == '/dlna/connection-manager/control':
                status, response = dlna.connection_manager_action(action, body)
            else:
                self.send_error(404)
                return
            self.reply(response, 'text/xml; charset=utf-8', status)
        except (ValueError, TimeoutError):
            self.send_error(400)


class HttpServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Relay:
    def __init__(self, folders, host, udn):
        self.library = Library(folders)
        self.stopped = threading.Event()
        self.udn = udn
        boot_id = int(time.time())
        self.http = HttpServer(('127.0.0.1', 0), Handler)
        self.http.relay = self
        self.http_thread = threading.Thread(target=self.http.serve_forever, args=(0.1,), daemon=True)
        self.http_thread.start()
        try:
            self.caddy = Caddy(host, self.http.server_port, udn)
            self.base = self.caddy.base
            self.content = dlna.ContentDirectory(self.library, self.base, boot_id)
            self.discovery = Discovery(host, self.base, udn, boot_id)
        except BaseException:
            self.stop()
            raise

    def stop(self):
        self.stopped.set()
        try:
            if hasattr(self, 'discovery'):
                self.discovery.stop()
        finally:
            try:
                if hasattr(self, 'caddy'):
                    self.caddy.stop()
            finally:
                self.http.shutdown()
                self.http.server_close()
                self.http_thread.join()
