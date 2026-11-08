"""Per-user settings for the standalone relay."""
import json
import os
import uuid
from pathlib import Path

from access import allowed_addresses

APP_DIR = Path(os.environ['LOCALAPPDATA']) / 'Local Media Relay'
STATE = APP_DIR / 'settings.json'
LOG_DIR = APP_DIR / 'logs'


def read_settings():
    try:
        data = json.loads(STATE.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError('Invalid saved settings.')
    return data


def load():
    data = read_settings()
    if not data:
        return [], 'uuid:' + str(uuid.uuid4())
    folders = data['folders']
    if not isinstance(folders, list) or not all(isinstance(folder, str) for folder in folders):
        raise ValueError('Invalid saved folder list.')
    return folders, 'uuid:' + str(uuid.UUID(data['udn'].removeprefix('uuid:')))


def load_access():
    return allowed_addresses(read_settings().get('allowed_clients'))


def write_settings(data):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    pending = STATE.with_name(f'{uuid.uuid4()}.tmp')
    try:
        pending.write_text(json.dumps(data, indent=2), encoding='utf-8')
        pending.replace(STATE)
    finally:
        pending.unlink(missing_ok=True)


def save(folders, udn):
    data = read_settings()
    data.update(folders=list(folders), udn=udn)
    write_settings(data)


def save_access(allowed_clients):
    addresses = allowed_addresses(allowed_clients)
    data = read_settings()
    data.setdefault('folders', [])
    data.setdefault('udn', 'uuid:' + str(uuid.uuid4()))
    data['allowed_clients'] = addresses
    write_settings(data)


def load_preview_generation():
    value = read_settings().get('generate_previews', True)
    if not isinstance(value, bool):
        raise ValueError('Invalid preview-generation setting.')
    return value


def save_preview_generation(enabled):
    if not isinstance(enabled, bool):
        raise ValueError('Preview-generation setting must be true or false.')
    data = read_settings()
    data.setdefault('folders', [])
    data.setdefault('udn', 'uuid:' + str(uuid.uuid4()))
    data['generate_previews'] = enabled
    write_settings(data)
