"""Updater checks requiring only the Python standard library."""
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import update_manager
import updater


class UpdaterTests(unittest.TestCase):
    def test_repository_normalization(self):
        self.assertEqual(update_manager.normalize_repo('https://github.com/Test-User/habbo-chat-reader.git'), 'Test-User/habbo-chat-reader')
        self.assertEqual(update_manager.normalize_repo('Test-User/habbo-chat-reader'), 'Test-User/habbo-chat-reader')
        for bad in ('', 'username', 'github.com/x/y/extra', 'a/../../evil', 'x/foo?api=1'):
            with self.subTest(bad=bad):
                with self.assertRaises(update_manager.UpdateError):
                    update_manager.normalize_repo(bad)

    def test_version_comparison(self):
        self.assertGreater(update_manager.parse_version('v8.2.1'), update_manager.parse_version('8.2.0'))
        for bad in ('v8', 'latest', '8.2.0.dev1', '8.2'):
            with self.assertRaises(update_manager.UpdateError):
                update_manager.parse_version(bad)

    def make_zip(self, version='8.3.0', missing=()):
        buf = io.BytesIO()
        contents = {'VERSION': version.encode(), 'habbo_reader.py': b'print("new")',
                    'update_manager.py': b'new', 'updater.py': b'new', 'local_tts.py': b'new'}
        with zipfile.ZipFile(buf, 'w') as z:
            for name, payload in contents.items():
                if name not in missing:
                    z.writestr('repo-main/' + name, payload)
            z.writestr('repo-main/unrelated_private_file', 'SHOULD NEVER BE INSTALLED')
            z.writestr('repo-main/.venv/Lib/something.py', 'NEVER INSTALL')
        return buf.getvalue()

    def test_archive_validate_and_allowlist(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / 'download.zip'
            src.write_bytes(self.make_zip())
            files = updater.version_files(src, '8.3.0')
            self.assertIn('VERSION', files)
            self.assertNotIn('.venv/Lib/something.py', files)
            self.assertNotIn('unrelated_private_file', files)
            with self.assertRaises(updater.InstallError):
                updater.version_files(src, '8.3.1')
            src.write_bytes(self.make_zip(missing=['updater.py']))
            with self.assertRaises(updater.InstallError):
                updater.version_files(src, '8.3.0')

    def test_install_preserves_other_files_and_backup(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / 'app'
            folder.mkdir()
            (folder / 'habbo_reader.py').write_text('old')
            (folder / '.venv').mkdir()
            (folder / '.venv' / 'python.exe').write_text('KEEP')
            (folder / 'voice-model.onnx').write_bytes(b'KEEP')
            files = {'habbo_reader.py': b'new', 'VERSION': b'8.3.0', 'requirements.txt': b'ok'}
            backup, needs_setup = updater.install(files, folder, Path(td) / 'backups')
            self.assertEqual((folder / 'habbo_reader.py').read_bytes(), b'new')
            self.assertEqual((backup / 'habbo_reader.py').read_text(), 'old')
            self.assertEqual((folder / 'voice-model.onnx').read_bytes(), b'KEEP')
            self.assertEqual((folder / '.venv' / 'python.exe').read_text(), 'KEEP')
            self.assertTrue(needs_setup)

    def test_rollback_restores_old_code(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td) / 'app'
            folder.mkdir()
            (folder / 'habbo_reader.py').write_text('old')
            (folder / 'VERSION').write_text('8.2.0')
            data = {'VERSION': b'8.3.0', 'habbo_reader.py': b'new'}
            original_replace = updater.os.replace
            counter = {'count': 0}
            def fail_replace(src, dst):
                counter['count'] += 1
                if counter['count'] == 2:
                    raise PermissionError('simulated lock')
                return original_replace(src, dst)
            with patch.object(updater.os, 'replace', side_effect=fail_replace):
                with self.assertRaises(PermissionError):
                    updater.install(data, folder, Path(td) / 'backups')
            self.assertEqual((folder / 'VERSION').read_text(), '8.2.0')
            self.assertEqual((folder / 'habbo_reader.py').read_text(), 'old')

    def test_public_version_network_mock(self):
        with patch.object(update_manager, '_fetch_bytes', return_value=b'8.3.0\n') as fake:
            self.assertEqual(update_manager.check_update('Person/habbo-chat-reader'), '8.3.0')
            self.assertIn('raw.githubusercontent.com', fake.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
