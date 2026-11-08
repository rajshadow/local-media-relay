"""Offline regressions for filesystem, discovery, and UPnP contracts."""
import os
import socket
import subprocess
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path, PureWindowsPath
from unittest.mock import MagicMock, patch

import dlna
from library import Library
from ssdp import Discovery
from windows_job import ChildJob


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'shared'
        self.root.mkdir()
        self.library = Library([self.root])

    def test_rejects_traversal(self):
        for part in ('..', '.. ', 'C:', 'sub\\folder'):
            with self.subTest(part=part), self.assertRaises(ValueError):
                self.library.path(f'0/{part}/video.mp4')

    @unittest.skipUnless(os.name == 'nt', 'Windows junction test')
    def test_rejects_replaced_shared_root(self):
        self.root.rename(self.base / 'original')
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'video.mp4').touch()
        subprocess.run(
            ['cmd.exe', '/d', '/c', 'mklink', '/J', 'shared', 'outside'],
            cwd=self.base, check=True, capture_output=True,
        )
        self.addCleanup(self.root.rmdir)
        with self.assertRaises(ValueError):
            self.library.path('0/video.mp4')

    def test_polling_detects_changes_in_browsed_folder(self):
        folder = self.root / 'nested'
        folder.mkdir()
        directory = dlna.ContentDirectory(self.library, 'http://127.0.0.1:1234', 7)
        body = (f'<Browse><ObjectID>{dlna.token("0/nested")}</ObjectID>'
                '<BrowseFlag>BrowseDirectChildren</BrowseFlag></Browse>').encode()
        status, first = directory.content_action('Browse', body)
        self.assertEqual(status, 200)
        before = ET.fromstring(first).findtext('.//UpdateID')
        (folder / 'new.mp4').touch()
        # Ensure a detectable stamp even on filesystems with coarse timestamps.
        stamp = folder.stat().st_mtime_ns + 2_000_000_000
        os.utime(folder, ns=(stamp, stamp))
        _, update = directory.content_action('GetSystemUpdateID', b'')
        self.assertNotEqual(ET.fromstring(update).findtext('.//Id'), before)
        _, after = directory.content_action('Browse', body)
        self.assertIn('new.mp4', ET.fromstring(after).findtext('.//Result'))

    def test_protected_system_folder_does_not_break_drive_root_browse(self):
        protected = self.root / 'System Volume Information'
        protected.mkdir()
        (self.root / 'pictures').mkdir()
        (self.root / 'photo.png').write_bytes(b'image')
        scan = self.library._scan_children

        def scan_with_system_permissions(path, key):
            if path == protected:
                raise PermissionError('Protected Windows system folder')
            return scan(path, key)

        catalog = dlna.ContentDirectory(self.library, 'http://127.0.0.1:1234', 1)
        with patch.object(self.library, '_scan_children', side_effect=scan_with_system_permissions):
            body = (f'<Browse><ObjectID>{dlna.token("0")}</ObjectID>'
                    '<BrowseFlag>BrowseDirectChildren</BrowseFlag></Browse>').encode()
            status, response = catalog.content_action('Browse', body)
            self.assertEqual(status, 200)
            entries = ET.fromstring(ET.fromstring(response).findtext('.//Result'))
            self.assertEqual(len(entries), 3)
            system = next(item for item in entries if item.findtext('{*}title') == protected.name)
            self.assertEqual(system.attrib['childCount'], '0')
            self.assertEqual(self.library.children('0/System Volume Information'), [])

    def test_root_labels_omit_indexes_and_disambiguate_identical_names(self):
        self.assertEqual(self.library.title('0'), self.root.name)
        first = self.base / 'one' / 'Photos'
        second = self.base / 'two' / 'Photos'
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        library = Library([first, second])
        self.assertEqual(library.title('0'), str(first.resolve()))
        self.assertEqual(library.title('1'), str(second.resolve()))
        drive = PureWindowsPath('E:/')
        with patch.object(self.library, 'roots', [drive]), patch.object(self.library, 'path', return_value=drive):
            self.assertEqual(self.library.title('0'), 'E:\\')


class ProtocolTests(unittest.TestCase):
    def test_advertises_polling_instead_of_event_subscriptions(self):
        device = ET.fromstring(dlna.device_xml('uuid:test'))
        self.assertEqual([entry.text for entry in device.findall('.//{*}eventSubURL')], [None, None])

    def test_connection_manager_describes_and_returns_default_connection(self):
        scpd = ET.fromstring(dlna.CONNECTION_MANAGER_SCPD)
        actions = {entry.findtext('{*}name'): entry for entry in scpd.findall('.//{*}action')}
        arguments = actions['GetProtocolInfo'].findall('.//{*}argument/{*}name')
        self.assertEqual([argument.text for argument in arguments], ['Source', 'Sink'])
        _, response = dlna.connection_manager_action('GetCurrentConnectionIDs', b'')
        self.assertEqual(ET.fromstring(response).findtext('.//ConnectionIDs'), '0')
        for number, status in ((0, 200), (1, 500)):
            body = f'<Action><ConnectionID>{number}</ConnectionID></Action>'.encode()
            self.assertEqual(dlna.connection_manager_action('GetCurrentConnectionInfo', body)[0], status)

    def test_oversized_packet_does_not_stop_discovery(self):
        session = Discovery.__new__(Discovery)
        session.receiver = MagicMock()
        session.sender = MagicMock()
        session.stopped = threading.Event()
        session.error = None
        session.targets = [('upnp:rootdevice', 'uuid:test::upnp:rootdevice')]
        session.base = 'http://127.0.0.1:1234'
        session.boot_id = 1
        address = ('127.0.0.1', 12345)
        packets = iter([
            b'M-SEARCH ' + b' ' * 5000,
            b'M-SEARCH * HTTP/1.1\r\nMAN: "ssdp:discover"\r\nST: upnp:rootdevice\r\n',
        ])

        def receive(size):
            self.assertGreater(size, 5000)
            packet = next(packets, None)
            if packet is not None:
                return packet, address
            session.stopped.set()
            raise socket.timeout()

        session.receiver.recvfrom.side_effect = receive
        session.run()
        self.assertIsNone(session.error)
        replies = [args[0] for args, _ in session.sender.sendto.call_args_list if args[1] == address]
        self.assertEqual(len(replies), 1)
        self.assertTrue(replies[0].startswith(b'HTTP/1.1 200 OK'))


@unittest.skipUnless(os.name == 'nt', 'Windows Job Object test')
class JobTests(unittest.TestCase):
    def test_close_terminates_owned_child(self):
        import sys

        job = ChildJob()
        self.addCleanup(job.close)
        with subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']) as process:
            try:
                job.attach(process)
                self.assertIsNone(process.poll())
                job.close()
                process.wait(timeout=5)
            finally:
                if process.poll() is None:
                    process.kill()
