"""Regressions for explicit client access and discovery privacy."""
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import settings
from access import allowed_addresses
from caddy_backend import Caddy
from ssdp import SSDP_ADDR, Discovery


class AccessTests(unittest.TestCase):
    def test_normalizes_exact_addresses_and_rejects_ranges(self):
        self.assertIsNone(allowed_addresses(None))
        self.assertEqual(
            allowed_addresses('192.168.1.20, 192.168.1.3 192.168.1.20'),
            ('192.168.1.3', '192.168.1.20'),
        )
        for value in ('', [], '192.168.1.0/24', 'host.local', '::1', '0.0.0.0', '239.1.2.3'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                allowed_addresses(value)

    def test_folder_edits_preserve_access_and_older_settings_migrate(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(settings, 'STATE', Path(directory) / 'settings.json'):
            udn = 'uuid:7d147f76-b681-4e5b-a665-bbf357e40cc9'
            settings.save(['before'], udn)
            self.assertIsNone(settings.load_access())
            settings.save_access(('192.168.1.3',))
            settings.save(['after'], udn)
            self.assertEqual(settings.load(), (['after'], udn))
            self.assertEqual(settings.load_access(), ('192.168.1.3',))
            settings.save_access(None)
            self.assertEqual(settings.load(), (['after'], udn))
            self.assertIsNone(settings.load_access())

    def test_invalid_saved_restricted_list_does_not_become_unrestricted(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(settings, 'STATE', Path(directory) / 'settings.json'):
            settings.STATE.write_text(json.dumps({'allowed_clients': []}), encoding='utf-8')
            with self.assertRaises(ValueError):
                settings.load_access()

    def test_restricted_caddy_uses_peer_gate_before_media_and_catalog(self):
        config = Caddy.config('192.168.1.2', 12345, 12346, ['192.168.1.3'], 'a' * 32)
        gate = config.index('@blocked not remote_ip 192.168.1.3')
        self.assertLess(gate, config.index('handle @media'))
        self.assertLess(gate, config.index('handle /dlna/*'))
        self.assertIn('header_up X-Relay-Client {remote_host}', config)
        self.assertNotIn('client_ip ', config)
        self.assertIn('remote_ip 192.168.1.2', config[:gate])
        unrestricted = Caddy.config('192.168.1.2', 12345, 12346)
        self.assertNotIn('@blocked', unrestricted)

    def test_restricted_discovery_only_replies_to_approved_peers(self):
        session = Discovery.__new__(Discovery)
        session.receiver = MagicMock()
        session.sender = MagicMock()
        session.allowed_clients = ('192.168.1.3',)
        session.stopped = threading.Event()
        session.error = None
        session.targets = [('upnp:rootdevice', 'uuid:test::upnp:rootdevice')]
        session.base = 'http://192.168.1.2:1234'
        session.boot_id = 1
        packet = b'M-SEARCH * HTTP/1.1\r\nMAN: "ssdp:discover"\r\nST: upnp:rootdevice\r\n'
        packets = iter([(packet, ('192.168.1.4', 12345)), (packet, ('192.168.1.3', 12345))])

        def receive(size):
            next_packet = next(packets, None)
            if next_packet is not None:
                return next_packet
            session.stopped.set()
            raise socket.timeout()

        session.receiver.recvfrom.side_effect = receive
        session.run()
        self.assertIsNone(session.error)
        destinations = [args[1] for args, _ in session.sender.sendto.call_args_list]
        self.assertEqual(destinations, [('192.168.1.3', 12345)])
        self.assertNotIn(SSDP_ADDR, destinations)
