"""Persistent playback positions keyed by media identity and client/profile."""
import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

MAX_SECONDS = 31 * 24 * 60 * 60
MAX_STATE_BYTES = 4 * 1024 * 1024
GUARD_SECONDS = 20


class CheckpointError(ValueError):
    """Saved checkpoints cannot be read safely. The file is left intact."""


def valid_seconds(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value <= MAX_SECONDS
        and math.isfinite(value)
    )


class Checkpoints:
    def __init__(self, state_path, max_entries=10000):
        if not isinstance(max_entries, int) or not 1 <= max_entries <= 10000:
            raise ValueError('Checkpoint capacity must be between 1 and 10000.')
        self.path = Path(state_path)
        self.max_entries = max_entries
        self.lock = threading.Lock()
        self.recent = {}
        self._load()

    def _key(self, path, client):
        if not isinstance(client, str) or not client.strip() or len(client) > 256:
            raise ValueError('A client or profile identifier is required.')
        media = Path(path).resolve(strict=True)
        if not media.is_file():
            raise ValueError('A checkpoint requires an existing file.')
        info = media.stat()
        identity = [os.path.normcase(str(media)), info.st_size, info.st_mtime_ns, client]
        return hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()

    def _load(self):
        try:
            if self.path.stat().st_size > MAX_STATE_BYTES:
                raise CheckpointError('Checkpoint file exceeds the supported size.')
            raw = self.path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return {}
        except UnicodeError as exc:
            raise CheckpointError(f'Cannot read checkpoint file {self.path}.') from exc
        try:
            state = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise CheckpointError(f'Cannot read checkpoint file {self.path}.') from exc
        if not isinstance(state, dict) or type(state.get('version')) is not int or state['version'] != 1 or not isinstance(state.get('items'), dict):
            raise CheckpointError(f'Unsupported checkpoint structure in {self.path}.')
        items = {}
        for key, value in state['items'].items():
            if (
                isinstance(key, str) and re.fullmatch(r'[0-9a-f]{64}', key)
                and isinstance(value, dict) and valid_seconds(value.get('seconds'))
                and isinstance(value.get('updated'), (int, float))
                and not isinstance(value['updated'], bool) and 0 <= value['updated'] <= 253402300799
                and math.isfinite(value['updated'])
            ):
                items[key] = {'seconds': float(value['seconds']), 'updated': float(value['updated'])}
        ordered = sorted(items, key=lambda key: (items[key]['updated'], key), reverse=True)
        return {key: items[key] for key in ordered[:self.max_entries]}

    @contextmanager
    def _locked(self):
        # Each session reloads inside this lock so two app instances cannot
        # overwrite one another's checkpoint updates.
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.with_suffix(self.path.suffix + '.lock').open('a+b') as lock_file:
                if lock_file.seek(0, os.SEEK_END) == 0:
                    lock_file.write(b'\0')
                    lock_file.flush()
                lock_file.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield self._load()
                finally:
                    lock_file.seek(0)
                    if os.name == 'nt':
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _save(self, items):
        ordered = sorted(items, key=lambda key: (items[key]['updated'], key), reverse=True)
        bounded = {key: items[key] for key in ordered[:self.max_entries]}
        pending = self.path.with_name(f'{self.path.name}.{uuid.uuid4().hex}.tmp')
        try:
            with pending.open('x', encoding='utf-8') as output:
                json.dump({'version': 1, 'items': bounded}, output, allow_nan=False, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            pending.replace(self.path)
        finally:
            pending.unlink(missing_ok=True)

    def _remember(self, key):
        self.recent.pop(key, None)
        self.recent[key] = time.monotonic()
        if len(self.recent) > self.max_entries:
            self.recent.pop(next(iter(self.recent)))

    def get(self, path, client):
        key = self._key(path, client)
        with self._locked() as items:
            item = items.get(key)
            if item is None:
                return 0.0
            self._remember(key)
            return item['seconds']

    def set(self, path, client, seconds):
        if not valid_seconds(seconds):
            raise ValueError(f'Checkpoint seconds must be finite and between 0 and {MAX_SECONDS}.')
        key = self._key(path, client)
        with self._locked() as items:
            prior = items.get(key)
            if prior is not None:
                recent_read = time.monotonic() - self.recent.get(key, -math.inf)
                recent_write = time.time() - prior['updated']
                if prior['seconds'] >= 30 and seconds <= 3 and (
                    0 <= recent_read <= GUARD_SECONDS or 0 <= recent_write <= GUARD_SECONDS
                ):
                    return False
                if prior['seconds'] == seconds:
                    return False
            items[key] = {'seconds': float(seconds), 'updated': time.time()}
            self._save(items)
            self._remember(key)
            return True

    def reset(self, path, client):
        key = self._key(path, client)
        with self._locked() as items:
            if key not in items:
                return False
            del items[key]
            self._save(items)
            self.recent.pop(key, None)
            return True
