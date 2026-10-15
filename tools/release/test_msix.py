"""Packaging boundary tests; these do not claim installation or GUI validation."""
from pathlib import Path
import json
import os
import struct
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

import msix


def fake_pe(path: Path, machine: int = 0x8664) -> None:
    header = bytearray(90)
    header[:2] = b'MZ'
    struct.pack_into('<I', header, 60, 64)
    header[64:68] = b'PE\0\0'
    struct.pack_into('<H', header, 68, machine)
    struct.pack_into('<H', header, 88, 0x20B)
    path.write_bytes(header)


class MsixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = {'name': 'Cutokyo.PackagingTest', 'publisher': 'CN=Cutokyo Test',
                         'publisher_display_name': 'Test & Development'}
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.binaries = self.root / 'bin'
        self.binaries.mkdir()
        for name in ('cutokyo.exe', 'cutokyo-desktop.exe'):
            fake_pe(self.binaries / name)
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir()
        fake_pe(self.runtime / 'msedgewebview2.exe')
        for name in ('icudtl.dat', 'resources.pak', 'locales/en-US.pak'):
            path = self.runtime / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'test fixture, not a redistributable runtime')

    def test_store_version_rejects_semver_and_reserved_revision(self) -> None:
        for version in ('0.1.0.0', '1.2.3', '1.0.0.1', '1.0.65536.0', '1.0.0-beta.0', '01.0.0.0'):
            with self.subTest(version=version), self.assertRaises(ValueError):
                msix.validate_identity(self.identity, version)

    def test_identity_is_required_and_xml_is_escaped(self) -> None:
        for invalid in ({}, {'name': '../Cutokyo'}, {'publisher': 'developer@example.com'},
                        {'name': 'REPLACE_WITH_Name'}, {'publisher_display_name': 'bad\x00name'}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                msix.validate_identity({**self.identity, **invalid} if invalid else {}, '1.0.0.0')
        self.identity['publisher'] = 'CN="Test & Development"'
        root = ET.fromstring(msix.manifest(self.identity, '1.0.0.0'))
        ns = {'f': 'http://schemas.microsoft.com/appx/manifest/foundation/windows10'}
        self.assertEqual(root.find('f:Identity', ns).attrib['Publisher'], self.identity['publisher'])
        self.assertEqual(root.find('f:Properties/f:PublisherDisplayName', ns).text, 'Test & Development')

    def test_wrong_architecture_or_missing_cli_refuses_before_staging(self) -> None:
        for machine in (0x14C, 0xAA64):
            fake_pe(self.binaries / 'cutokyo.exe', machine)
            with self.assertRaises(ValueError):
                msix.stage(self.identity, '1.0.0.0', self.binaries, self.runtime, self.root / 'out')
            self.assertFalse((self.root / 'out').exists())
        (self.binaries / 'cutokyo.exe').unlink()
        with self.assertRaises(OSError):
            msix.stage(self.identity, '1.0.0.0', self.binaries, self.runtime, self.root / 'out')

    def test_incomplete_runtime_is_rejected(self) -> None:
        (self.runtime / 'resources.pak').unlink()
        with self.assertRaises(ValueError):
            msix.validate_runtime(self.runtime)

    def test_only_required_payload_is_staged_and_output_is_not_overwritten(self) -> None:
        (self.binaries / 'private.pfx').write_bytes(b'must not ship')
        (self.binaries / 'debug.pdb').write_bytes(b'must not ship')
        output = self.root / 'out'
        msix.stage(self.identity, '1.0.0.0', self.binaries, self.runtime, output)
        self.assertEqual({p.name for p in output.iterdir()}, {
            'cutokyo.exe', 'cutokyo-desktop.exe', 'Assets', 'WebView2', 'LICENSE.txt',
            'NOTICE.txt', 'THIRD_PARTY.md', 'AppxManifest.xml'})
        ns = {'f': 'http://schemas.microsoft.com/appx/manifest/foundation/windows10',
              'uap': 'http://schemas.microsoft.com/appx/manifest/uap/windows10',
              'uap5': 'http://schemas.microsoft.com/appx/manifest/uap/windows10/5'}
        root = ET.parse(output / 'AppxManifest.xml').getroot()
        applications = root.findall('f:Applications/f:Application', ns)
        self.assertEqual({app.attrib['Executable'] for app in applications},
                         {'cutokyo.exe', 'cutokyo-desktop.exe'})
        # Store rejects any hidden application without a HeadlessAppBypass waiver.
        for app in applications:
            self.assertEqual(app.find('uap:VisualElements', ns).get('AppListEntry', 'default'),
                             'default')
        cli = next(app for app in applications if app.attrib['Executable'] == 'cutokyo.exe')
        self.assertEqual(cli.find('.//uap5:ExecutionAlias', ns).attrib['Alias'], 'cutokyo.exe')
        with self.assertRaises(FileExistsError):
            msix.stage(self.identity, '1.0.0.0', self.binaries, self.runtime, output)

    @unittest.skipUnless(os.name == 'nt', 'MakeAppx schema validation requires the Windows SDK')
    def test_windows_sdk_accepts_manifest_and_layout(self) -> None:
        sdk = Path(os.environ['ProgramFiles(x86)']) / 'Windows Kits/10/bin'
        candidates = sorted(sdk.glob('10.0.*/x64/makeappx.exe'),
                            key=lambda path: tuple(map(int, path.parent.parent.name.split('.'))))
        self.assertTrue(candidates, 'Install Windows SDK MakeAppx.exe to validate the manifest')
        output = self.root / 'layout'
        registered = json.loads((Path(__file__).with_name('msix-identity.json')).read_text())
        msix.stage(registered, '1.0.0.0', self.binaries, self.runtime, output)
        # Synthetic PE fixtures only: this proves SDK schema validation, not that
        # the application was built, installed, launched or certified.
        result = subprocess.run([str(candidates[-1]), 'pack', '/d', str(output),
                                 '/p', str(self.root / 'schema-test.msix'), '/o'],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
