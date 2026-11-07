"""Bounded background previews with a disk cache and optional JPEG sidecars."""
import hashlib
import os
import shutil
import stat
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from image_thumbs import MAX_THUMBNAIL_BYTES, ImageThumbnailMaps
from library import IMAGE_EXTS, VIDEO_EXTS, is_media
from windows_job import ChildJob

AUTO_FFMPEG = object()
PREVIEW_VERSION = 'jpeg-320x180-v1'


def jpeg_preview(path):
    """Accept small ordinary JPEG files, rejecting links and partial writes."""
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_THUMBNAIL_BYTES:
            return False
        if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            return False
        with path.open('rb') as image:
            if image.read(2) != b'\xff\xd8':
                return False
            image.seek(-2, 2)
            return image.read(2) == b'\xff\xd9'
    except (OSError, ValueError):
        return False


class ThumbnailManager:
    def __init__(self, cache_dir, library, ffmpeg=AUTO_FFMPEG, workers=2, pending_limit=8):
        self.cache_dir = Path(cache_dir)
        self.library = library
        self.image_maps = ImageThumbnailMaps(library)
        self.ffmpeg = shutil.which('ffmpeg') if ffmpeg is AUTO_FFMPEG else ffmpeg
        self.pending_limit = pending_limit
        self._job = ChildJob() if os.name == 'nt' and self.ffmpeg else None
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='preview')
        self._lock = threading.Lock()
        self._closed = False
        self._pending = set()
        self._processes = set()
        self._failures = {}

    def cache_path(self, source):
        info = source.stat()
        identity = f'{source.resolve()}|{info.st_size}|{info.st_mtime_ns}|{PREVIEW_VERSION}'
        digest = hashlib.blake2s(identity.encode('utf-8'), digest_size=16).hexdigest()
        return self.cache_dir / f'{digest}.jpg'

    def sidecar(self, source):
        source = source.resolve(strict=True)
        for index, root in enumerate(self.library.roots):
            try:
                relative = source.relative_to(root)
            except ValueError:
                continue
            key = f'{index}/{relative.as_posix()}.thumb.jpg'
            try:
                candidate = self.library.path(key)
                if jpeg_preview(candidate):
                    return candidate
            except (OSError, ValueError):
                pass
        return None

    def pre_generated(self, source):
        source = Path(source)
        if source.suffix.lower() in IMAGE_EXTS:
            return self.image_maps.thumbnail(source)
        return None

    def advertise(self, source):
        if self.pre_generated(source) is not None:
            return True
        return self.request(source) is not None or bool(self.ffmpeg)

    def preview_bytes(self, source):
        image = self.pre_generated(source)
        if image is not None:
            return image
        path = self.request(source)
        if path is None:
            return None
        with path.open('rb') as stream:
            image = stream.read(MAX_THUMBNAIL_BYTES + 1)
        return image if len(image) <= MAX_THUMBNAIL_BYTES else None

    def request(self, source):
        """Return an existing preview, or queue work and return immediately."""
        source = Path(source)
        if not is_media(source):
            return None
        try:
            sidecar = self.sidecar(source)
            if sidecar is not None:
                if sidecar.stat().st_mtime_ns >= source.stat().st_mtime_ns:
                    return sidecar
            cache = self.cache_path(source)
            if jpeg_preview(cache):
                return cache
        except (OSError, ValueError):
            return None
        with self._lock:
            retry_at = self._failures.get(cache, 0)
            if (self._closed or not self.ffmpeg or cache in self._pending
                    or len(self._pending) >= self.pending_limit or time.monotonic() < retry_at):
                return None
            self._pending.add(cache)
            self._executor.submit(self._generate, source, cache)
        return None

    def _generate(self, source, cache):
        temporary = None
        success = False
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            # One manager owns each cache entry while it is being generated.
            temporary = cache.with_name(f'{cache.stem}.{os.getpid()}.{id(self)}.tmp.jpg')
            success = self._render(source, temporary)
            if success and jpeg_preview(temporary) and self.cache_path(source) == cache:
                temporary.replace(cache)
            else:
                success = False
        except (OSError, ValueError):
            success = False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            with self._lock:
                self._pending.discard(cache)
                if not success:
                    if len(self._failures) >= 256:
                        self._failures.pop(next(iter(self._failures)))
                    self._failures[cache] = time.monotonic() + 60

    def _render(self, source, destination):
        arguments = [self.ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error',
                     '-protocol_whitelist', 'file,pipe']
        if source.suffix.lower() in VIDEO_EXTS:
            arguments += ['-ss', '1']
        arguments += ['-i', str(source), '-frames:v', '1', '-an', '-vf',
                      'scale=320:180:force_original_aspect_ratio=decrease', '-q:v', '4', '-y', str(destination)]
        with self._lock:
            if self._closed:
                return False
            process = subprocess.Popen(
                arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            try:
                if self._job is not None:
                    self._job.attach(process)
            except OSError:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                    raise
            self._processes.add(process)
        try:
            return process.wait(timeout=20) == 0
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            return False
        finally:
            with self._lock:
                self._processes.discard(process)

    def close(self):
        with self._lock:
            self._closed = True
            if self._job is not None:
                self._job.close()
            else:
                for process in self._processes:
                    if process.poll() is None:
                        process.terminate()
        self._executor.shutdown(wait=True, cancel_futures=True)
