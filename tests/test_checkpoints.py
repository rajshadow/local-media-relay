"""Checkpoint persistence, identities, input validation, and write isolation."""
import concurrent.futures
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from checkpoints import MAX_SECONDS, CheckpointError, Checkpoints


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.media = self.root / 'clip.mp4'
        self.media.write_bytes(b'test media')
        self.state = self.root / 'state' / 'checkpoints.json'
        self.store = Checkpoints(self.state)

    def test_round_trip_and_client_isolation(self):
        self.assertEqual(self.store.get(self.media, 'tv'), 0)
        self.assertTrue(self.store.set(self.media, 'tv', 123.75))
        self.assertTrue(self.store.set(self.media, 'headset', 42))
        reloaded = Checkpoints(self.state)
        self.assertEqual(reloaded.get(self.media, 'tv'), 123.75)
        self.assertEqual(reloaded.get(self.media, 'headset'), 42)
        self.assertEqual(reloaded.get(self.media, 'unknown'), 0)
        self.assertNotIn('clip.mp4', self.state.read_text(encoding='utf-8'))

    def test_changed_media_does_not_inherit_position(self):
        self.store.set(self.media, 'tv', 150)
        self.media.write_bytes(b'a replaced clip with a different length')
        self.assertEqual(self.store.get(self.media, 'tv'), 0)

    def test_mtime_change_invalidates_even_when_size_is_unchanged(self):
        self.store.set(self.media, 'tv', 150)
        info = self.media.stat()
        os.utime(self.media, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))
        self.assertEqual(self.store.get(self.media, 'tv'), 0)

    def test_near_zero_after_resume_preserves_position_until_explicit_reset(self):
        self.store.set(self.media, 'tv', 150)
        self.assertEqual(self.store.get(self.media, 'tv'), 150)
        self.assertFalse(self.store.set(self.media, 'tv', 0))
        self.assertEqual(self.store.get(self.media, 'tv'), 150)
        self.assertTrue(self.store.reset(self.media, 'tv'))
        self.assertEqual(self.store.get(self.media, 'tv'), 0)
        self.assertFalse(self.store.reset(self.media, 'tv'))

    def test_near_zero_after_guard_window_is_accepted(self):
        self.store.set(self.media, 'tv', 150)
        wall, monotonic = time.time(), time.monotonic()
        with patch('checkpoints.time.time', return_value=wall + 21), patch(
            'checkpoints.time.monotonic', return_value=monotonic + 21
        ):
            self.assertTrue(self.store.set(self.media, 'tv', 2))
        self.assertEqual(self.store.get(self.media, 'tv'), 2)

    def test_rejects_invalid_positions_and_clients(self):
        for value in (-1, math.nan, math.inf, '12', True, MAX_SECONDS + 1, 10**400):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.set(self.media, 'tv', value)
        for client in ('', ' ', None, 'x' * 257):
            with self.subTest(client=client), self.assertRaises(ValueError):
                self.store.set(self.media, client, 12)

    def test_corrupt_state_is_left_intact(self):
        self.state.parent.mkdir()
        corrupt = b'{unfinished'
        self.state.write_bytes(corrupt)
        with self.assertRaises(CheckpointError):
            Checkpoints(self.state)
        self.assertEqual(self.state.read_bytes(), corrupt)
        with self.assertRaises(CheckpointError):
            self.store.set(self.media, 'tv', 10)
        self.assertEqual(self.state.read_bytes(), corrupt)

    def test_invalid_rows_are_discarded_and_capacity_is_bounded(self):
        self.state.parent.mkdir()
        items = {
            'a' * 64: {'seconds': 10, 'updated': 1},
            'b' * 64: {'seconds': 20, 'updated': 2},
            'c' * 64: {'seconds': math.nan, 'updated': 3},
            'd' * 64: {'seconds': 10, 'updated': 10**400},
        }
        self.state.write_text(json.dumps({'version': 1, 'items': items}), encoding='utf-8')
        store = Checkpoints(self.state, max_entries=2)
        self.assertTrue(store.set(self.media, 'tv', 30))
        loaded = json.loads(self.state.read_text(encoding='utf-8'))['items']
        self.assertEqual(len(loaded), 2)
        self.assertNotIn('a' * 64, loaded)
        self.assertNotIn('c' * 64, loaded)
        self.assertNotIn('d' * 64, loaded)

    def test_failed_atomic_replace_preserves_previous_state(self):
        self.store.set(self.media, 'tv', 150)
        previous = self.state.read_bytes()
        with patch('checkpoints.Path.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.set(self.media, 'tv', 200)
        self.assertEqual(self.state.read_bytes(), previous)
        self.assertEqual(self.store.get(self.media, 'tv'), 150)
        self.assertEqual(list(self.state.parent.glob('*.tmp')), [])

    def test_independent_instances_merge_concurrent_updates(self):
        stores = [self.store, Checkpoints(self.state)]

        def save(index):
            stores[index % 2].set(self.media, f'client-{index}', index + 10)

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(save, range(12)))
        reloaded = Checkpoints(self.state)
        for index in range(12):
            self.assertEqual(reloaded.get(self.media, f'client-{index}'), index + 10)

    def test_two_processes_do_not_lose_each_others_updates(self):
        worker = (
            'import sys; from checkpoints import Checkpoints; '
            'store = Checkpoints(sys.argv[1]); '
            '[(store.set(sys.argv[2], sys.argv[3] + str(i), i + 20)) for i in range(5)]'
        )
        with subprocess.Popen(
            [sys.executable, '-B', '-c', worker, str(self.state), str(self.media), 'first-'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ) as first, subprocess.Popen(
            [sys.executable, '-B', '-c', worker, str(self.state), str(self.media), 'second-'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ) as second:
            _, first_error = first.communicate(timeout=20)
            _, second_error = second.communicate(timeout=20)
            self.assertEqual(first.returncode, 0, first_error.decode(errors='replace'))
            self.assertEqual(second.returncode, 0, second_error.decode(errors='replace'))
        for prefix in ('first-', 'second-'):
            for index in range(5):
                self.assertEqual(self.store.get(self.media, prefix + str(index)), index + 20)
