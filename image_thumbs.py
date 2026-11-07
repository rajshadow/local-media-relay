"""Read pre-generated JPEG thumbnail maps with bounded, shared caches."""
import base64
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024
MAX_MAP_FILE_BYTES = 64 * 1024 * 1024
MAX_MAP_CACHE_BYTES = 128 * 1024 * 1024
MAX_DECODED_BYTES = 32 * 1024 * 1024
CHECK_INTERVAL = 2.0


@dataclass
class MapData:
    stamp: tuple
    values: dict
    size: int


class ImageThumbnailMaps:
    def __init__(self, library):
        self.library = library
        self.lock = threading.RLock()
        self.folders = OrderedDict()
        self.maps = OrderedDict()
        self.decoded = OrderedDict()
        self.map_bytes = 0
        self.decoded_bytes = 0

    def _folder_key(self, source):
        source = Path(source).resolve(strict=True)
        for index, root in enumerate(self.library.roots):
            try:
                relative = source.parent.relative_to(root)
            except ValueError:
                continue
            return str(index) if relative == Path('.') else f'{index}/{relative.as_posix()}'
        return None

    def _selected(self, folder_key):
        now = time.monotonic()
        cached = self.folders.pop(folder_key, None)
        if cached is not None and now < cached[0]:
            self.folders[folder_key] = cached
            return cached[1]
        folder = self.library.path(folder_key)
        selected = None
        newest = -1
        for candidate in folder.glob('thumbs_*.json'):
            try:
                candidate = self.library.path(f'{folder_key}/{candidate.name}')
                info = candidate.stat()
                if candidate.is_file() and info.st_mtime_ns > newest:
                    selected = candidate
                    newest = info.st_mtime_ns
            except (ValueError, OSError):
                continue
        stamp = None
        if selected is not None:
            info = selected.stat()
            stamp = (selected, info.st_size, info.st_mtime_ns)
        self.folders[folder_key] = (now + CHECK_INTERVAL, stamp)
        while len(self.folders) > 256:
            self.folders.popitem(last=False)
        return stamp

    def _map(self, stamp):
        path, size, _ = stamp
        cached = self.maps.pop(path, None)
        if cached is not None:
            self.map_bytes -= cached.size
            if cached.stamp == stamp:
                self.maps[path] = cached
                self.map_bytes += cached.size
                return cached
        if size > MAX_MAP_FILE_BYTES:
            data = MapData(stamp, {}, 0)
        else:
            try:
                with path.open('rb') as stream:
                    raw = stream.read(MAX_MAP_FILE_BYTES + 1)
                if len(raw) > MAX_MAP_FILE_BYTES:
                    raise ValueError('Thumbnail map exceeds the size limit')
                values = json.loads(raw)
                if not isinstance(values, dict):
                    raise ValueError('Thumbnail map must contain filename entries')
                limit = (MAX_THUMBNAIL_BYTES + 2) // 3 * 4 + 64
                values = {name.lower(): value for name, value in values.items()
                          if isinstance(value, str) and 0 < len(value) <= limit}
                data = MapData(stamp, values, len(raw))
            except (OSError, ValueError, RecursionError):
                data = MapData(stamp, {}, 0)
        self.maps[path] = data
        self.map_bytes += data.size
        while len(self.maps) > 4 or self.map_bytes > MAX_MAP_CACHE_BYTES:
            _, evicted = self.maps.popitem(last=False)
            self.map_bytes -= evicted.size
        return data

    def thumbnail(self, source):
        source = Path(source)
        try:
            folder_key = self._folder_key(source)
            if folder_key is None:
                return None
            # Keep discovery, parsing and decoding single-flight when players
            # issue duplicate Browse and artwork requests concurrently.
            with self.lock:
                stamp = self._selected(folder_key)
                if stamp is None or stamp[2] < source.stat().st_mtime_ns:
                    return None
                data = self._map(stamp)
                value = data.values.get(source.name.lower())
                if value is None:
                    return None
                key = stamp, source.name.lower()
                cached = self.decoded.pop(key, None)
                if cached is not None:
                    self.decoded[key] = cached
                    return cached
                if value[:5].lower() == 'data:':
                    prefix, value = value.split(',', 1)
                    if prefix.lower() != 'data:image/jpeg;base64':
                        return None
                image = base64.b64decode(value, validate=True)
                if (len(image) > MAX_THUMBNAIL_BYTES or not image.startswith(b'\xff\xd8')
                        or not image.endswith(b'\xff\xd9')):
                    return None
                self.decoded[key] = image
                self.decoded_bytes += len(image)
                while len(self.decoded) > 2048 or self.decoded_bytes > MAX_DECODED_BYTES:
                    _, evicted = self.decoded.popitem(last=False)
                    self.decoded_bytes -= len(evicted)
                return image
        except (OSError, ValueError):
            return None
