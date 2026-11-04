"""Read-only folder mapping for DLNA object IDs and media URLs."""
import re
import stat
from pathlib import Path


VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}


class Library:
    def __init__(self, folders):
        self.roots = [Path(folder).resolve(strict=True) for folder in folders]
        if not self.roots or any(not root.is_dir() for root in self.roots):
            raise ValueError('Choose at least one existing folder.')

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
        children = []
        for item in sorted(path.iterdir(), key=lambda entry: entry.name.casefold()):
            child = f'{key}/{item.name}'
            try:
                candidate = self.path(child)
                if candidate.is_dir() or (candidate.is_file() and candidate.suffix.lower() in VIDEO_EXTS):
                    children.append(child)
            except (OSError, ValueError):
                continue
        return children

    def title(self, key):
        if not key:
            return 'Local Media Relay'
        path = self.path(key)
        name = path.name or str(path)
        return f'{name} ({int(key) + 1})' if '/' not in key else name

    def is_dir(self, key):
        return not key or self.path(key).is_dir()
