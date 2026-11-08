"""Resource ownership when a session cannot finish starting."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import settings
from relay import Relay


class StartupTests(unittest.TestCase):
    def test_bind_failure_closes_preview_manager(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(settings, 'APP_DIR', Path(directory)):
                with patch('relay.ThumbnailManager') as previews:
                    with patch('relay.HttpServer', side_effect=OSError('cannot bind')):
                        with self.assertRaises(OSError):
                            Relay([directory], '127.0.0.1', 'uuid:test')
                    previews.return_value.close.assert_called_once()

    def test_thread_failure_closes_socket_without_shutdown_deadlock(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(settings, 'APP_DIR', Path(directory)):
                with patch('relay.ThumbnailManager') as previews, patch('relay.HttpServer') as http:
                    with patch('relay.threading.Thread') as threads:
                        threads.return_value.start.side_effect = RuntimeError('cannot start')
                        threads.return_value.is_alive.return_value = False
                        threads.return_value.ident = None
                        with self.assertRaises(RuntimeError):
                            Relay([directory], '127.0.0.1', 'uuid:test')
                    http.return_value.server_close.assert_called_once()
                    http.return_value.shutdown.assert_not_called()
                    previews.return_value.close.assert_called_once()

    def test_generation_can_be_disabled_at_the_session_entry_point(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(settings, 'APP_DIR', Path(directory)):
            with patch('relay.ThumbnailManager') as previews, patch('relay.HttpServer', side_effect=OSError('cannot bind')):
                with self.assertRaises(OSError):
                    Relay([directory], '127.0.0.1', 'uuid:test', generate_previews=False)
                self.assertIsNone(previews.call_args.kwargs['ffmpeg'])
                previews.return_value.close.assert_called_once()
