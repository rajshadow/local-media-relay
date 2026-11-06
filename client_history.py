"""Summarize recently observed HTTP clients from the relay's access logs."""
import heapq
import ipaddress
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

HISTORY_DAYS = 30
MAX_FILES = 32
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_LINE_BYTES = 64 * 1024
MAX_CLIENTS = 200


@dataclass(frozen=True)
class SeenClient:
    address: str
    user_agent: str
    last_seen: float

    @property
    def guess(self):
        return device_guess(self.user_agent)


def device_guess(user_agent):
    agent = user_agent.casefold()
    for tokens, label in (
        (('quest',), 'Possible Quest headset'),
        (('samsung', 'sec_hhp'), 'Possible Samsung device'),
        (('webos', 'web0s'), 'Possible LG/webOS device'),
        (('sony', 'bravia'), 'Possible Sony device'),
        (('playstation',), 'Possible PlayStation'),
        (('xbox',), 'Possible Xbox'),
        (('deovr',), 'DeoVR player; device unknown'),
        (('vlc',), 'VLC player; device unknown'),
        (('android',), 'Android client; device unknown'),
    ):
        if any(token in agent for token in tokens):
            return label
    return 'Unknown device'


def client_from_event(event, now):
    if not isinstance(event, dict):
        return None
    timestamp = event.get('ts')
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        return None
    try:
        timestamp = float(timestamp)
    except OverflowError:
        return None
    if not math.isfinite(timestamp) or timestamp < now - HISTORY_DAYS * 86400 or timestamp > now + 300:
        return None
    request = event.get('request')
    if not isinstance(request, dict):
        return None
    uri = request.get('uri')
    if not isinstance(uri, str) or not uri.startswith(('/dlna/', '/media/')):
        return None
    if not isinstance(request.get('remote_ip'), str):
        return None
    try:
        address = ipaddress.IPv4Address(request['remote_ip'])
    except (ValueError, TypeError):
        return None
    if address.is_loopback or address.is_unspecified or address.is_multicast or int(address) == 0xffffffff:
        return None
    user_agent = ''
    headers = request.get('headers')
    if isinstance(headers, dict):
        for name, value in headers.items():
            if not isinstance(name, str) or name.casefold() != 'user-agent':
                continue
            if isinstance(value, list):
                value = next((item for item in value if isinstance(item, str)), '')
            if isinstance(value, str):
                user_agent = ''.join(character for character in value[:512] if character.isprintable())
            break
    return SeenClient(str(address), user_agent, float(timestamp))


def recent_clients(log_dir, now=None):
    """Read bounded log tails. Return clients and a count of unreadable files."""
    now = time.time() if now is None else now
    candidates = []
    unreadable = 0
    try:
        for path in Path(log_dir).glob('*.jsonl'):
            try:
                if path.is_file():
                    candidates.append((path.stat().st_mtime_ns, path))
            except OSError:
                unreadable += 1
    except OSError:
        return [], 1
    latest = heapq.nlargest(MAX_FILES, candidates)
    clients = {}
    remaining = MAX_TOTAL_BYTES
    for _, path in latest:
        if remaining <= 0:
            break
        try:
            with path.open('rb') as stream:
                size = stream.seek(0, 2)
                budget = min(size, MAX_FILE_BYTES, remaining)
                remaining -= budget
                stream.seek(size - budget)
                if size > budget:
                    # Discard the first partial line when reading a file tail.
                    fragment = stream.readline(min(MAX_LINE_BYTES + 1, size - stream.tell()))
                    while fragment and not fragment.endswith(b'\n') and stream.tell() < size:
                        fragment = stream.readline(min(MAX_LINE_BYTES + 1, size - stream.tell()))
                while stream.tell() < size:
                    line = stream.readline(min(MAX_LINE_BYTES + 1, size - stream.tell()))
                    if not line:
                        break
                    if len(line) > MAX_LINE_BYTES:
                        # Consume the rest of an oversized line in bounded chunks.
                        while line and not line.endswith(b'\n') and stream.tell() < size:
                            line = stream.readline(min(MAX_LINE_BYTES + 1, size - stream.tell()))
                        continue
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeError, RecursionError):
                        continue
                    client = client_from_event(event, now)
                    if client is None:
                        continue
                    key = client.address, client.user_agent
                    prior = clients.get(key)
                    if prior is None or client.last_seen > prior.last_seen:
                        clients[key] = client
                    if len(clients) > MAX_CLIENTS:
                        oldest = min(clients, key=lambda item: clients[item].last_seen)
                        del clients[oldest]
        except OSError:
            unreadable += 1
    return sorted(clients.values(), key=lambda client: (-client.last_seen, client.address, client.user_agent)), unreadable
