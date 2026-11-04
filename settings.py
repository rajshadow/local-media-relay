"""Per-user settings for the standalone relay."""
import json
import os
import uuid
from pathlib import Path

APP_DIR = Path(os.environ['LOCALAPPDATA']) / 'Local Media Relay'
STATE = APP_DIR / 'settings.json'
LOG_DIR = APP_DIR / 'logs'


def load():
    try:
        data = json.loads(STATE.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return [], 'uuid:' + str(uuid.uuid4())
    folders = data['folders']
    if not isinstance(folders, list) or not all(isinstance(folder, str) for folder in folders):
        raise ValueError('Invalid saved folder list.')
    return folders, 'uuid:' + str(uuid.UUID(data['udn'].removeprefix('uuid:')))


def save(folders, udn):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    pending = STATE.with_name(f'{uuid.uuid4()}.tmp')
    try:
        pending.write_text(json.dumps({'folders': folders, 'udn': udn}, indent=2), encoding='utf-8')
        pending.replace(STATE)
    finally:
        pending.unlink(missing_ok=True)
