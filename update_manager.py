"""Opt-in GitHub update discovery and download for Habbo Chat Reader.

This module only contacts GitHub if a public repository has been configured.
No Habbo screenshots, messages, voices, or configuration are ever uploaded.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

APP_NAME = 'HabboChatReader'
try:
    VERSION = (Path(__file__).resolve().parent / 'VERSION').read_text(encoding='utf-8').strip()
except OSError:
    VERSION = '8.2.0'  # Fallback if the version file is missing
UPDATE_CONFIG = Path(os.environ.get('APPDATA') or Path.home()) / APP_NAME / 'updater.json'
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
REPO_RE = re.compile(r'^[A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]{1,100}$')


class UpdateError(Exception):
    pass


def normalize_repo(text: str) -> str:
    v = (text or '').strip().strip('/')
    for prefix in ('https://github.com/', 'http://github.com/', 'github.com/'):
        if v.lower().startswith(prefix):
            v = v[len(prefix):].strip('/')
            break
    if v.endswith('.git'):
        v = v[:-4]
    if not REPO_RE.fullmatch(v) or '..' in v.split('/')[1]:
        raise UpdateError('Enter a GitHub repository like USERNAME/habbo-chat-reader (public repository).')
    return v


def parse_version(value: str) -> tuple[int, int, int]:
    text = value.strip().lstrip('vV')
    if not re.fullmatch(r'\d+\.\d+\.\d+', text):
        raise UpdateError(f'Invalid version number: {value[:60]!r}. Expected e.g. 8.2.1')
    return tuple(int(part) for part in text.split('.'))


def load_repo() -> str:
    try:
        data = json.loads(UPDATE_CONFIG.read_text(encoding='utf-8'))
        return normalize_repo(data['repository'])
    except (OSError, KeyError, TypeError, ValueError, UpdateError):
        return ''


def save_repo(repo: str) -> None:
    repo = normalize_repo(repo)
    UPDATE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temp = UPDATE_CONFIG.with_suffix('.tmp')
    temp.write_text(json.dumps({'repository': repo}, indent=2), encoding='utf-8')
    temp.replace(UPDATE_CONFIG)


def _fetch_bytes(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={'User-Agent': f'HabboChatReader/{VERSION}', 'Accept':'application/octet-stream'})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            content = response.read(limit + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError('Repository or VERSION file not found. Make the repository public and upload the app files to the main branch.') from exc
        raise UpdateError(f'GitHub returned HTTP {exc.code}') from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f'Could not reach GitHub: {exc}') from exc
    if len(content) > limit:
        raise UpdateError('GitHub download too large. The repository must contain only the source code, not voice models.')
    return content


def latest_version(repo: str) -> str:
    repo = normalize_repo(repo)
    raw = _fetch_bytes(f'https://raw.githubusercontent.com/{repo}/main/VERSION', 128)
    result = raw.decode('utf-8-sig').strip()
    parse_version(result)
    return result


def check_update(repo: str) -> str | None:
    current = parse_version(VERSION)
    remote = latest_version(repo)
    return remote if parse_version(remote) > current else None


def download_update(repo: str, version: str) -> Path:
    repo = normalize_repo(repo)
    parse_version(version)
    payload = _fetch_bytes(f'https://codeload.github.com/{repo}/zip/refs/heads/main', MAX_ARCHIVE_BYTES)
    if not payload.startswith(b'PK\x03\x04'):
        raise UpdateError('GitHub did not return a ZIP archive.')
    import zipfile
    from io import BytesIO
    with zipfile.ZipFile(BytesIO(payload)) as zipped:
        found = [(name, zipped.read(name)) for name in zipped.namelist() if name.endswith('/VERSION') and name.count('/') == 1]
        if len(found) != 1 or found[0][1].decode('utf-8-sig').strip() != version:
            raise UpdateError('The remote version changed during download. Retry the update.')
    dest = Path(tempfile.mkdtemp(prefix='habbo-update-')) / 'update.zip'
    dest.write_bytes(payload)
    return dest
