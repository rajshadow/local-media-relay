"""Observed clients remain suggestions until the user selects addresses."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import client_history
from app import App
from client_history import (
    SeenClient,
    client_from_event,
    device_guess,
    local_ipv4_addresses,
    recent_clients,
)

NOW = 1_800_000_000


def event(address='192.168.1.20', agent='Samsung SEC_HHP_[TV]', timestamp=NOW, uri='/dlna/device.xml'):
    return {'ts': timestamp, 'request': {
        'remote_ip': address, 'uri': uri, 'headers': {'User-Agent': [agent]},
    }}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.logs = Path(self.temp.name)

    def write_log(self, events, name='session.jsonl'):
        path = self.logs / name
        path.write_text('\n'.join(json.dumps(item) for item in events) + '\n', encoding='utf-8')
        return path

    def test_merges_sessions_and_keeps_reports_in_one_row_per_address(self):
        self.write_log([event(timestamp=NOW - 20)], 'first.jsonl')
        self.write_log([
            event(timestamp=NOW - 10), event(timestamp=NOW),
            event(agent='VLC/3'), event(address='192.168.1.21', agent='Quest player'),
        ], 'second.jsonl')
        clients, unreadable = recent_clients(self.logs, NOW)
        self.assertEqual(len(clients), 2)
        self.assertEqual(unreadable, 0)
        samsung = next(client for client in clients if 'Samsung' in client.user_agent)
        self.assertEqual(samsung.last_seen, NOW)
        self.assertEqual(samsung.guess, 'Possible Samsung device')
        self.assertEqual(sum(client.address == '192.168.1.20' for client in clients), 1)
        self.assertIn('VLC/3', samsung.evidence)

    def test_ignores_healthchecks_old_events_and_untrusted_forwarding_headers(self):
        events = [
            event(uri='/_relay-health/nonce'), event(address='127.0.0.1'),
            event(address='::1'), event(address='0.0.0.0'), event(address='239.1.2.3'),
            event(timestamp=NOW - 31 * 86400), event(timestamp=NOW + 301),
            {'level': 'info', 'msg': 'Caddy started'},
        ]
        actual = event(address='192.168.1.22')
        actual['request']['headers']['X-Forwarded-For'] = ['192.168.1.99']
        events.append(actual)
        self.write_log(events)
        clients, _ = recent_clients(self.logs, NOW)
        self.assertEqual([client.address for client in clients], ['192.168.1.22'])

    def test_bad_lines_and_partial_writes_do_not_hide_valid_clients(self):
        path = self.write_log([event()])
        with path.open('a', encoding='utf-8') as stream:
            stream.write('not json\n')
            stream.write(json.dumps(event(agent='unknown')) + '\n')
            stream.write('{"ts":')
        clients, _ = recent_clients(self.logs, NOW)
        self.assertEqual(len(clients), 1)

    def test_oversized_line_is_skipped_and_next_event_is_read(self):
        path = self.logs / 'large.jsonl'
        path.write_bytes(b'x' * (client_history.MAX_LINE_BYTES + 100) + b'\n'
                         + json.dumps(event()).encode() + b'\n')
        clients, _ = recent_clients(self.logs, NOW)
        self.assertEqual(len(clients), 1)

    def test_tail_limit_skips_first_partial_line(self):
        path = self.logs / 'tail.jsonl'
        last = json.dumps(event()).encode() + b'\n'
        path.write_bytes(b'x' * 1000 + b'\n' + last)
        with patch.object(client_history, 'MAX_FILE_BYTES', len(last) + 10):
            clients, _ = recent_clients(self.logs, NOW)
        self.assertEqual(len(clients), 1)

    def test_tail_does_not_parse_a_fragment_of_an_oversized_line(self):
        fake = json.dumps(event(address='192.168.1.99')).encode() + b'\n'
        wanted = json.dumps(event()).encode() + b'\n'
        fragment = b'x' * (client_history.MAX_LINE_BYTES + 1) + fake
        path = self.logs / 'tail.jsonl'
        path.write_bytes(b'padding' + fragment + wanted)
        with patch.object(client_history, 'MAX_FILE_BYTES', len(fragment) + len(wanted)):
            clients, _ = recent_clients(self.logs, NOW)
        self.assertEqual([client.address for client in clients], ['192.168.1.20'])

    def test_result_count_is_bounded_and_recent_clients_are_kept(self):
        self.write_log([event(address=f'192.168.1.{index}', timestamp=NOW - index) for index in range(1, 8)])
        with patch.object(client_history, 'MAX_CLIENTS', 3):
            clients, _ = recent_clients(self.logs, NOW)
        self.assertEqual([client.address for client in clients],
                         ['192.168.1.1', '192.168.1.2', '192.168.1.3'])

    def test_invalid_types_and_extreme_timestamps_are_ignored(self):
        for timestamp in (True, 'today', None, float('nan'), float('inf'), 10**400):
            with self.subTest(timestamp=timestamp):
                self.assertIsNone(client_from_event(event(timestamp=timestamp), NOW))
        self.assertIsNone(client_from_event(event(address=12345), NOW))
        malformed = event()
        malformed['request'] = []
        self.assertIsNone(client_from_event(malformed, NOW))

    def test_missing_or_generic_headers_produce_an_unknown_device(self):
        record = event()
        record['request']['headers'] = None
        client = client_from_event(record, NOW)
        self.assertEqual(client.user_agent, '')
        self.assertEqual(client.guess, 'Unknown device')
        self.assertEqual(device_guess('Mozilla/5.0'), 'Unknown device')
        self.assertEqual(device_guess('DeoVR/1.0'), 'DeoVR player; device unknown')

    def test_tv_without_media_headers_keeps_samsung_hint_and_media_activity(self):
        self.write_log([
            event(uri='/dlna/content-directory/control', timestamp=NOW - 10),
            event(agent='', uri='/media/0/video.mp4', timestamp=NOW),
        ])
        clients, _ = recent_clients(self.logs, NOW)
        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0].guess, 'Possible Samsung device')
        self.assertEqual(clients[0].activity, 'Media requested')
        self.assertEqual(clients[0].last_seen, NOW)

    def test_passive_description_fetches_are_distinct_from_browsing(self):
        self.write_log([
            event(agent='DAFUPnP'), event(agent='Microsoft-Windows/10.0 UPnP/1.0'),
            event(address='192.168.1.21', agent='', uri='/dlna/content-directory/control'),
        ])
        clients, _ = recent_clients(self.logs, NOW)
        windows = next(client for client in clients if client.address == '192.168.1.20')
        self.assertEqual(windows.guess, 'Possible Windows device')
        self.assertEqual(windows.activity, 'Description only')
        self.assertIn('DAFUPnP', windows.evidence)
        self.assertEqual(clients[1].activity, 'Library request')

    def test_conflicting_device_reports_are_visible_for_a_reused_ip(self):
        self.write_log([event(), event(agent='Quest player')])
        clients, _ = recent_clients(self.logs, NOW)
        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0].guess, 'Mixed device reports')
        self.assertIn('Quest player', clients[0].evidence)
        self.assertIn('Samsung', clients[0].evidence)

    def test_excludes_this_pc_before_grouping(self):
        self.write_log([
            event(address='192.168.1.16', agent='Python-urllib/3.13'),
            event(address='10.0.0.2', agent='Microsoft-Windows/10.0'),
            event(address='192.168.1.8', agent='Samsung'),
        ])
        clients, _ = recent_clients(self.logs, NOW, exclude_addresses={'192.168.1.16', '10.0.0.2'})
        self.assertEqual([client.address for client in clients], ['192.168.1.8'])

    def test_unreadable_files_are_reported_without_discarding_history(self):
        self.write_log([event()])
        with patch.object(Path, 'open', side_effect=PermissionError('locked')):
            clients, unreadable = recent_clients(self.logs, NOW)
        self.assertEqual(clients, [])
        self.assertEqual(unreadable, 1)


class LocalAddressTests(unittest.TestCase):
    def test_includes_selected_host_and_addresses_from_multiple_interfaces(self):
        interfaces = [
            (2, 1, 6, '', ('192.168.1.16', 0)),
            (2, 1, 6, '', ('10.0.0.2', 0)),
        ]
        with patch('client_history.socket.getaddrinfo', return_value=interfaces):
            self.assertEqual(local_ipv4_addresses('192.168.1.99'),
                             {'192.168.1.16', '10.0.0.2', '192.168.1.99'})

    def test_selected_host_is_excluded_even_if_hostname_lookup_fails(self):
        with patch('client_history.socket.getaddrinfo', side_effect=OSError('no DNS')):
            self.assertEqual(local_ipv4_addresses('192.168.1.16'), {'192.168.1.16'})
            self.assertEqual(local_ipv4_addresses('invalid edit'), set())


class SelectionTests(unittest.TestCase):
    def make_app(self):
        app = App.__new__(App)
        app.relay = None
        app.window = Mock()
        app.client_ips = Mock()
        app.client_ips.get.return_value = '192.168.1.2'
        app.restricted = Mock()
        app.update_access_controls = Mock()
        app.history_note = Mock()
        app.clients = Mock()
        app.clients.selection.return_value = ('a', 'b')
        app.seen_clients = {
            'a': SeenClient('192.168.1.20', 'Samsung', NOW),
            'b': SeenClient('192.168.1.20', 'VLC', NOW),
        }
        return app

    def test_explicit_selection_merges_ips_without_saving_or_starting(self):
        app = self.make_app()
        with patch('settings.save_access') as save:
            app.add_selected_clients()
        app.client_ips.set.assert_called_once_with('192.168.1.2, 192.168.1.20')
        app.restricted.set.assert_called_once_with(True)
        app.update_access_controls.assert_called_once()
        save.assert_not_called()

    def test_empty_selection_and_active_sharing_leave_access_unchanged(self):
        app = self.make_app()
        app.clients.selection.return_value = ()
        app.add_selected_clients()
        app.clients.selection.return_value = ('a',)
        app.relay = object()
        app.add_selected_clients()
        app.client_ips.set.assert_not_called()
        app.restricted.set.assert_not_called()

    def test_invalid_manual_addresses_are_preserved_for_correction(self):
        app = self.make_app()
        app.client_ips.get.return_value = 'invalid manual entry'
        with patch('app.messagebox.showerror') as error:
            app.add_selected_clients()
        app.client_ips.set.assert_not_called()
        app.restricted.set.assert_not_called()
        error.assert_called_once()
