"""Read-only folder mapping for DLNA object IDs and media URLs."""
import re
import stat
import threading
import time
from collections import OrderedDict
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
MEDIA_EXTS = VIDEO_EXTS | IMAGE_EXTS
MAX_CACHED_CHILDREN = 50000
DIRECTORY_CACHE_SECONDS = 2.0


def is_media(path):
    return path.suffix.lower() in MEDIA_EXTS and not path.name.lower().endswith('.thumb.jpg')


class Library:
    def __init__(self, folders):
        self.roots = [Path(folder).resolve(strict=True) for folder in folders]
        if not self.roots or any(not root.is_dir() for root in self.roots):
            raise ValueError('Choose at least one existing folder.')
        self._directory_lock = threading.Lock()
        self._directories = OrderedDict()
        self._cached_children = 0

    def path(self, key):
        if not key:
            return None
        index, *parts = key.split('/')
        if not re.fullmatch(r'[0-9]{1,6}', index) or int(index) >= len(self.roots):
            raise ValueError('Invalid folder')
        path = self.roots[int(index)]
        if path.resolve(strict=True) != path or not path.is_dir():
            raise ValueError('The selected folder changed while sharing')
        for part in parts:
            if not part or part.endswith(('.', ' ')) or any(c in part for c in '\\:\x00'):
                raise ValueError('Invalid path')
            path = path / part
            info = path.lstat()
            if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError('Linked entries are skipped')
        return path

    def children(self, key):
        if not key:
            return [str(index) for index in range(len(self.roots))]
        path = self.path(key)
        if not path.is_dir():
            return []
        info = path.stat()
        stamp = info.st_ino, info.st_mtime_ns
        with self._directory_lock:
            cached = self._directories.pop((path, key), None)
            if cached is not None:
                self._cached_children -= len(cached[2])
                if cached[0] == stamp and time.monotonic() < cached[1]:
                    self._directories[path, key] = cached
                    self._cached_children += len(cached[2])
                    # The caller owns its list and cannot mutate the cache.
                    return list(cached[2])
            try:
                children = self._scan_children(path, key)
            except PermissionError:
                # Drive roots contain protected system folders. They should
                # appear empty instead of breaking their parent's whole page.
                return []
            if len(children) <= MAX_CACHED_CHILDREN:
                self._directories[path, key] = (stamp, time.monotonic() + DIRECTORY_CACHE_SECONDS,
                                           tuple(children))
                self._cached_children += len(children)
                while len(self._directories) > 128 or self._cached_children > MAX_CACHED_CHILDREN:
                    _, evicted = self._directories.popitem(last=False)
                    self._cached_children -= len(evicted[2])
            return children

    def _scan_children(self, path, key):
        children = []
        for item in path.iterdir():
            child = f'{key}/{item.name}'
            try:
                candidate = self.path(child)
                is_directory = candidate.is_dir()
                if is_directory or (candidate.is_file() and is_media(candidate)):
                    children.append((not is_directory, item.name.casefold(), child))
            except (OSError, ValueError):
                continue
        return [child for _, _, child in sorted(children)]

    def title(self, key):
        if not key:
            return 'Local Media Relay'
        path = self.path(key)
        name = path.name or str(path)
        if '/' not in key:
            duplicates = sum((root.name or str(root)).casefold() == name.casefold()
                             for root in self.roots)
            return str(path) if duplicates > 1 else name
        return name

    def is_dir(self, key):
        return not key or self.path(key).is_dir()
