"""Detached update installer. Stdlib-only, runs after the main app exits.

Updates only allowlisted application files from a GitHub repository ZIP.
Keeps .venv, voice models and user settings untouched. Backup+rollback on failure.
"""
from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
from datetime import datetime
from pathlib import Path

APP_FILES = (
    'VERSION', 'habbo_reader.py', 'bubble_ocr.py', 'reader_core.py',
    'speech_backend.py', 'local_tts.py', 'speaker_voices.py',
    'update_manager.py', 'updater.py',
    'requirements.txt', 'README.txt', 'Setup_and_Run.bat',
    'Start_Habbo_Reader.bat', 'Test_Audio_Only.bat', 'Test_Voices_Without_Habbo.bat'
)
REQUIRED_FILES = {'VERSION', 'habbo_reader.py', 'update_manager.py', 'updater.py', 'local_tts.py'}


class InstallError(Exception):
    pass


def wait_for_exit(pid: int, seconds: int = 35):
    if os.name == 'nt':
        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x00000102
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            try:
                state = ctypes.windll.kernel32.WaitForSingleObject(handle, seconds * 1000)
                if state == WAIT_TIMEOUT:
                    raise InstallError('Reader did not close in time. Close it and try again.')
                return
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
    # Fallback for non-Windows tests or when an exited PID cannot be opened.
    time.sleep(1.5)


def version_files(zip_path: Path, expected: str) -> dict[str, bytes]:
    if zip_path.stat().st_size > 16 * 1024 * 1024:
        raise InstallError('Update archive is too large.')
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        # GitHub branch snapshot ZIP has exactly one top-level directory.
        candidates = [n for n in names if n.endswith('/VERSION') and n.count('/') == 1]
        if len(candidates) != 1:
            raise InstallError('VERSION not found in repository root.')
        root = candidates[0][:-len('VERSION')]
        loaded = {}
        for name in APP_FILES:
            path = root + name
            if path not in names:
                continue
            meta = archive.getinfo(path)
            if meta.is_dir() or meta.file_size > 2_000_000:
                raise InstallError(f'Invalid file in archive: {name}')
            loaded[name] = archive.read(path)
        if not REQUIRED_FILES.issubset(loaded):
            raise InstallError('The repository is missing required app files.')
        if loaded['VERSION'].decode('utf-8-sig').strip() != expected:
            raise InstallError('Downloaded version does not match checked version.')
        if not loaded['habbo_reader.py'].strip():
            raise InstallError('Main application file is empty.')
    return loaded


def install(files: dict[str, bytes], destination: Path, backup_parent: Path) -> tuple[Path, bool]:
    """Stage all files first, back up originals, install; restore on any failure."""
    destination = destination.resolve(strict=True)
    if not (destination / 'habbo_reader.py').is_file():
        raise InstallError('Destination is not a Habbo Reader folder.')
    backup_parent.mkdir(parents=True, exist_ok=True)
    backup = backup_parent / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir()
    old_files = set()
    replaced = []
    requirements_changed = False
    with tempfile.TemporaryDirectory(prefix='habbo-stage-') as staging:
        staged = Path(staging)
        for name, payload in files.items():
            (staged / name).write_bytes(payload)
        for name in files:
            old = destination / name
            if old.is_file():
                old_files.add(name)
                shutil.copy2(old, backup / name)
        requirements_changed = files.get('requirements.txt') != ((backup / 'requirements.txt').read_bytes() if (backup / 'requirements.txt').is_file() else None)
        try:
            for name in files:
                os.replace(str(staged / name), str(destination / name))
                replaced.append(name)
        except BaseException:
            for name in reversed(replaced):
                try:
                    if name in old_files:
                        shutil.copy2(backup / name, destination / name)
                    else:
                        (destination / name).unlink(missing_ok=True)
                except OSError:
                    pass
            raise
    return backup, requirements_changed


def run(args):
    wait_for_exit(args.pid)
    data = version_files(Path(args.archive), args.version)
    target = Path(args.destination)
    backups = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'HabboChatReader' / 'backups'
    backup, needs_setup = install(data, target, backups)
    print(f'Installed Habbo Reader {args.version}. Previous app backup: {backup}', flush=True)
    launcher = target / ('Setup_and_Run.bat' if needs_setup else 'Start_Habbo_Reader.bat')
    if os.name == 'nt':
        os.startfile(str(launcher))
    else:
        print(f'Launch {launcher} to start the reader.')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--apply', action='store_true', required=True)
    p.add_argument('--pid', type=int, required=True)
    p.add_argument('--archive', required=True)
    p.add_argument('--destination', required=True)
    p.add_argument('--version', required=True)
    args = p.parse_args()
    try:
        run(args)
    except Exception:
        traceback.print_exc()
        print('\nUpdate failed. Your original installation may still be intact. Check the error above.')
        if os.name == 'nt':
            input('Press Enter to exit the updater...')
        raise


if __name__ == '__main__':
    main()
