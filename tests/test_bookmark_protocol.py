"""The client timestamp adapter uses Samsung milliseconds and stable media IDs."""
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from checkpoints import Checkpoints
from dlna import ContentDirectory, token
from library import Library


class BookmarkProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        (self.folder / 'video.mp4').write_bytes(b'video')
        (self.folder / 'photo.png').write_bytes(b'photo')
        self.store = Checkpoints(self.folder / 'state.json')
        self.catalog = ContentDirectory(Library([self.folder]), 'http://127.0.0.1:1234', 1,
                                        checkpoints=self.store)

    def set_bookmark(self, value, key='0/video.mp4', client='client-one'):
        body = (f'<Action><ObjectID>{token(key)}</ObjectID>'
                f'<PosSecond>{value}</PosSecond></Action>').encode()
        return self.catalog.content_action('X_SetBookmark', body, client)

    def test_milliseconds_round_trip_and_client_isolation(self):
        self.assertEqual(self.set_bookmark('125500')[0], 200)
        first = self.catalog.didl('0/video.mp4', 'client-one')
        other = self.catalog.didl('0/video.mp4', 'client-two')
        self.assertIn('BM=125500', first)
        self.assertIn('BM=0', other)
        self.assertEqual(self.store.get(self.folder / 'video.mp4', 'client-one'), 125.5)
        self.assertEqual(self.set_bookmark('0')[0], 200)
        self.assertIn('BM=125500', self.catalog.didl('0/video.mp4', 'client-one'))

    def test_invalid_values_and_photo_are_rejected(self):
        for value in ('nan', 'inf', '-1', 'junk'):
            with self.subTest(value=value):
                self.assertEqual(self.set_bookmark(value)[0], 500)
        status, response = self.set_bookmark('1000', key='0/photo.png')
        self.assertEqual(status, 500)
        self.assertEqual(ET.fromstring(response).findtext('.//{*}errorCode'), '701')
