"""Pre-generated map reuse and large-directory cache regressions."""
import base64
import concurrent.futures
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dlna
import image_thumbs
from library import Library
from thumbnails import ThumbnailManager

JPEG = b'\xff\xd8' + b'cached-preview' + b'\xff\xd9'
OTHER_JPEG = b'\xff\xd8' + b'updated-preview' + b'\xff\xd9'


class ImageOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()
        self.root = self.folder / 'media'
        self.root.mkdir()
        self.source = self.root / 'Photo.PNG'
        self.source.write_bytes(b'image-fixture')
        self.library = Library([self.root])
        self.manager = ThumbnailManager(self.folder / 'cache', self.library, ffmpeg=None)
        self.addCleanup(self.manager.close)

    def write_map(self, payload, name='thumbs_test.json'):
        path = self.root / name
        path.write_text(json.dumps(payload), encoding='utf-8')
        return path

    def test_map_serves_artwork_without_ffmpeg_or_cache_writes(self):
        self.write_map({'photo.png': base64.b64encode(JPEG).decode()})
        with patch.object(self.manager, '_render') as render:
            directory = dlna.ContentDirectory(self.library, 'http://127.0.0.1:1234', 7,
                                               thumbnails=self.manager)
            self.assertIn('albumArtURI', directory.didl('0/Photo.PNG'))
            self.assertEqual(self.manager.preview_bytes(self.source), JPEG)
            render.assert_not_called()
        self.assertFalse(self.manager.cache_dir.exists())

    def test_concurrent_requests_parse_map_once(self):
        self.write_map({'Photo.PNG': 'data:image/jpeg;base64,' + base64.b64encode(JPEG).decode()})
        with patch('image_thumbs.json.loads', wraps=json.loads) as load:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                result = list(pool.map(self.manager.preview_bytes, [self.source] * 24))
            self.assertEqual(result, [JPEG] * 24)
            self.assertEqual(load.call_count, 1)

    def test_changed_source_and_updated_map_invalidate_cached_preview(self):
        with patch.object(image_thumbs, 'CHECK_INTERVAL', 0):
            mapping = self.write_map({'photo.png': base64.b64encode(JPEG).decode()})
            self.assertEqual(self.manager.preview_bytes(self.source), JPEG)
            changed = mapping.stat().st_mtime_ns + 2_000_000_000
            os.utime(self.source, ns=(changed, changed))
            self.assertIsNone(self.manager.preview_bytes(self.source))
            self.write_map({'photo.png': base64.b64encode(OTHER_JPEG).decode()})
            os.utime(mapping, ns=(changed + 2_000_000_000, changed + 2_000_000_000))
            self.assertEqual(self.manager.preview_bytes(self.source), OTHER_JPEG)

    def test_newest_map_is_selected_and_invalid_entries_fall_back(self):
        old = self.write_map({'Photo.PNG': base64.b64encode(JPEG).decode()}, 'thumbs_old.json')
        latest = self.write_map({'Photo.PNG': base64.b64encode(OTHER_JPEG).decode()}, 'thumbs_new.json')
        stamp = max(old.stat().st_mtime_ns, latest.stat().st_mtime_ns)
        os.utime(latest, ns=(stamp + 1_000_000_000, stamp + 1_000_000_000))
        with patch.object(image_thumbs, 'CHECK_INTERVAL', 0):
            self.assertEqual(self.manager.preview_bytes(self.source), OTHER_JPEG)
            latest.write_text('{"photo.png": "not a JPEG"}', encoding='utf-8')
            os.utime(latest, ns=(stamp + 2_000_000_000, stamp + 2_000_000_000))
            self.assertIsNone(self.manager.preview_bytes(self.source))
            latest.write_text('broken JSON', encoding='utf-8')
            os.utime(latest, ns=(stamp + 3_000_000_000, stamp + 3_000_000_000))
            self.assertIsNone(self.manager.preview_bytes(self.source))

    def test_map_and_decoded_cache_limits_are_enforced(self):
        for index in range(5):
            root = self.root / str(index)
            root.mkdir()
            source = root / 'photo.png'
            source.write_bytes(b'image')
            (root / 'thumbs_test.json').write_text(
                json.dumps({'photo.png': base64.b64encode(JPEG).decode()}), encoding='utf-8')
            with patch.object(image_thumbs, 'MAX_MAP_CACHE_BYTES', 100), patch.object(
                    image_thumbs, 'MAX_DECODED_BYTES', len(JPEG)):
                self.assertEqual(self.manager.preview_bytes(source), JPEG)
                self.assertLessEqual(self.manager.image_maps.map_bytes, 100)
                self.assertLessEqual(self.manager.image_maps.decoded_bytes, len(JPEG))
        self.assertLessEqual(len(self.manager.image_maps.maps), 4)

    def test_oversized_map_is_not_parsed(self):
        mapping = self.write_map({'photo.png': base64.b64encode(JPEG).decode()})
        with patch.object(image_thumbs, 'MAX_MAP_FILE_BYTES', mapping.stat().st_size - 1):
            with patch('image_thumbs.json.loads') as load:
                self.assertIsNone(self.manager.preview_bytes(self.source))
                load.assert_not_called()

    def test_map_lookup_validates_selected_file_through_library(self):
        self.write_map({'photo.png': base64.b64encode(JPEG).decode()})
        original = self.library.path

        def validate(key):
            if key.endswith('.json'):
                raise ValueError('Linked entries are skipped')
            return original(key)

        with patch.object(self.library, 'path', side_effect=validate):
            self.assertIsNone(self.manager.preview_bytes(self.source))

    def test_repeated_pages_and_child_counts_share_directory_scan(self):
        nested = self.root / 'nested'
        nested.mkdir()
        for index in range(100):
            (nested / f'{index:04}.jpg').touch()
        directory = dlna.ContentDirectory(self.library, 'http://127.0.0.1:1234', 7)
        with patch.object(self.library, '_scan_children', wraps=self.library._scan_children) as scan:
            for _ in range(3):
                metadata = directory.didl('0/nested')
                self.assertIn('childCount="100"', metadata)
                body = (f'<Browse><ObjectID>{dlna.token("0/nested")}</ObjectID>'
                        '<BrowseFlag>BrowseDirectChildren</BrowseFlag>'
                        '<RequestedCount>10</RequestedCount></Browse>').encode()
                self.assertEqual(directory.content_action('Browse', body)[0], 200)
            self.assertEqual(scan.call_count, 1)
        first = self.library.children('0/nested')
        first.clear()
        self.assertEqual(len(self.library.children('0/nested')), 100)
        (nested / 'added.jpg').touch()
        stamp = nested.stat().st_mtime_ns + 2_000_000_000
        os.utime(nested, ns=(stamp, stamp))
        self.assertEqual(len(self.library.children('0/nested')), 101)
