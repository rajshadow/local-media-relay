"""Image catalog and preview cache regressions without a decoder dependency."""
import os
import tempfile
import threading
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import dlna
from library import Library
from thumbnails import MAX_THUMBNAIL_BYTES, ThumbnailManager, jpeg_preview

# Only the JPEG envelope matters to the cache-file checks in these unit tests.
JPEG = b'\xff\xd8' + b'preview-fixture' + b'\xff\xd9'


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.root = self.folder / 'media'
        self.root.mkdir()
        self.source = self.root / 'photo.png'
        self.source.write_bytes(b'image-fixture')
        self.library = Library([self.root])
        self.manager = ThumbnailManager(self.folder / 'cache', self.library, ffmpeg=None)
        self.addCleanup(self.manager.close)

    def test_image_catalog_keeps_original_mime_and_photo_class(self):
        (self.root / 'photo.png.thumb.jpg').write_bytes(JPEG)
        self.assertEqual(self.library.children('0'), ['0/photo.png'])
        directory = dlna.ContentDirectory(self.library, 'http://127.0.0.1:1234', 7, self.manager)
        item = ET.fromstring(dlna.didl_document([directory.didl('0/photo.png')]))[0]
        self.assertEqual(item.findtext('{*}class'), 'object.item.imageItem.photo')
        self.assertIn('image/png', item.find('{*}res').attrib['protocolInfo'])
        self.assertTrue(item.findtext('{*}albumArtURI').endswith('.jpg'))
        self.assertEqual(dlna.mime_for(Path('photo.JPEG')), 'image/jpeg')

    def test_cached_preview_works_without_ffmpeg_and_invalidates_on_change(self):
        cache = self.manager.cache_path(self.source)
        cache.parent.mkdir()
        cache.write_bytes(JPEG)
        self.assertEqual(self.manager.request(self.source), cache)
        self.source.write_bytes(b'replaced-image-with-a-different-size')
        self.assertIsNone(self.manager.request(self.source))

    def test_sidecar_lookup_normalizes_source_alias(self):
        sidecar = self.source.with_name(self.source.name + '.thumb.jpg')
        sidecar.write_bytes(JPEG)
        alias = self.source.parent / '..' / self.source.parent.name / self.source.name
        self.assertEqual(self.manager.request(alias), sidecar)

    def test_sidecar_is_reused_but_stale_or_oversized_files_are_ignored(self):
        sidecar = self.source.with_name(self.source.name + '.thumb.jpg')
        sidecar.write_bytes(JPEG)
        self.assertEqual(self.manager.request(self.source), sidecar)
        os.utime(sidecar, ns=(1, 1))
        self.assertIsNone(self.manager.request(self.source))
        sidecar.write_bytes(b'\xff\xd8' + b'x' * MAX_THUMBNAIL_BYTES + b'\xff\xd9')
        self.assertFalse(jpeg_preview(sidecar))
        self.assertIsNone(self.manager.request(self.source))

    @unittest.skipUnless(os.name == 'nt', 'Windows junction test')
    def test_sidecar_parent_junction_is_rejected(self):
        import subprocess

        nested = self.root / 'nested'
        nested.mkdir()
        source = nested / 'clip.mp4'
        source.write_bytes(b'video-fixture')
        moved = self.folder / 'outside'
        nested.rename(moved)
        (moved / 'clip.mp4.thumb.jpg').write_bytes(JPEG)
        subprocess.run(
            ['cmd.exe', '/d', '/c', 'mklink', '/J', 'nested', str(moved)],
            cwd=self.root, check=True, capture_output=True,
        )
        self.addCleanup(nested.rmdir)
        self.assertIsNone(self.manager.sidecar(source))

    def test_browsing_never_waits_for_generation_and_duplicate_requests_share_one_job(self):
        manager = ThumbnailManager(self.folder / 'generated', self.library, ffmpeg='test-ffmpeg')
        release = threading.Event()
        started = threading.Event()
        calls = []

        def render(source, destination):
            calls.append(source)
            started.set()
            release.wait(3)
            destination.write_bytes(JPEG)
            return True

        try:
            with patch.object(manager, '_render', side_effect=render):
                self.assertIsNone(manager.request(self.source))
                self.assertTrue(started.wait(3))
                directory = dlna.ContentDirectory(self.library, 'http://127.0.0.1:1234', 7, manager)
                self.assertIn('albumArtURI', directory.didl('0/photo.png'))
                for _ in range(4):
                    self.assertIsNone(manager.request(self.source))
                release.set()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    result = manager.request(self.source)
                    if result is not None:
                        break
                    time.sleep(0.01)
                self.assertEqual(result.read_bytes(), JPEG)
                self.assertEqual(calls, [self.source])
        finally:
            release.set()
            manager.close()

    @unittest.skipUnless(os.name == 'nt', 'Windows Job Object test')
    def test_close_terminates_active_preview_process(self):
        import subprocess
        import sys

        manager = ThumbnailManager(self.folder / 'owned', self.library, ffmpeg='test-ffmpeg')
        real_popen = subprocess.Popen
        started = threading.Event()
        children = []

        def launch(*args, **kwargs):
            process = real_popen([sys.executable, '-c', 'import time; time.sleep(30)'])
            children.append(process)
            started.set()
            return process

        try:
            with patch('thumbnails.subprocess.Popen', side_effect=launch):
                manager.request(self.source)
                self.assertTrue(started.wait(3))
                manager.close()
                self.assertIsNotNone(children[0].poll())
        finally:
            manager.close()
            for process in children:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)


    def test_nested_photo_folders_have_counts_and_precede_images_when_paged(self):
        nested = self.root / 'z nested'
        deep = nested / 'deeper & photos'
        deep.mkdir(parents=True)
        (deep / 'photo.png').write_bytes(b'deep-image-fixture')
        (nested / 'a picture.jpg').write_bytes(b'image-fixture')
        directory = dlna.ContentDirectory(self.library, 'http://127.0.0.1:1234', 7)
        key = '0'
        for expected_child, expected_count in (('0/z nested', 2),
                                               ('0/z nested/deeper & photos', 2)):
            body = (f'<Browse><ObjectID>{dlna.token(key)}</ObjectID>'
                    '<BrowseFlag>BrowseDirectChildren</BrowseFlag>'
                    '<StartingIndex>0</StartingIndex><RequestedCount>1</RequestedCount></Browse>').encode()
            status, response = directory.content_action('Browse', body)
            self.assertEqual(status, 200)
            soap = ET.fromstring(response)
            self.assertEqual(int(soap.findtext('.//TotalMatches')), expected_count)
            entries = ET.fromstring(soap.findtext('.//Result'))
            folder = entries[0]
            self.assertEqual(folder.tag.rsplit('}', 1)[-1], 'container')
            self.assertEqual(folder.attrib['id'], dlna.token(expected_child))
            self.assertGreater(int(folder.attrib['childCount']), 0)
            status, page = directory.content_action('Browse', body.replace(
                b'<StartingIndex>0</StartingIndex>', b'<StartingIndex>1</StartingIndex>'))
            self.assertEqual(status, 200)
            image = ET.fromstring(ET.fromstring(page).findtext('.//Result'))[0]
            self.assertEqual(image.findtext('{*}class'), 'object.item.imageItem.photo')
            key = expected_child
        body = (f'<Browse><ObjectID>{dlna.token(key)}</ObjectID>'
                '<BrowseFlag>BrowseDirectChildren</BrowseFlag></Browse>').encode()
        status, response = directory.content_action('Browse', body)
        self.assertEqual(status, 200)
        photo = ET.fromstring(ET.fromstring(response).findtext('.//Result'))[0]
        self.assertEqual(photo.findtext('{*}class'), 'object.item.imageItem.photo')
        self.assertEqual(photo.attrib['parentID'], dlna.token(key))
        self.assertIn('/media/0/z%20nested/deeper%20%26%20photos/photo.png', photo.findtext('{*}res'))

    def test_browse_advertises_missing_artwork_without_starting_disk_jobs(self):
        video = self.root / 'large-video.mp4'
        video.write_bytes(b'video-fixture')
        manager = ThumbnailManager(self.folder / 'lazy', self.library, ffmpeg='test-ffmpeg')
        try:
            directory = dlna.ContentDirectory(self.library, 'http://127.0.0.1:1234', 7, manager)
            with patch.object(manager._executor, 'submit') as submit:
                self.assertIn('albumArtURI', directory.didl('0/large-video.mp4'))
                submit.assert_not_called()
                self.assertEqual(manager._pending, set())
        finally:
            manager.close()
