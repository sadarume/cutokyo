#!/usr/bin/env python3
"""Validate Store identity and stage an x64 desktop/CLI MSIX (no publication)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import struct
import sys
from xml.sax.saxutils import escape, quoteattr

ROOT = Path(__file__).resolve().parents[2]
ICONS = ("Square44x44Logo.png", "Square150x150Logo.png", "StoreLogo.png")


def validate_identity(identity: dict, version: str) -> None:
    for key in ("name", "publisher", "publisher_display_name"):
        value = identity.get(key)
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError(f"identity.{key} must be copied from Partner Center")
        if "REPLACE_WITH" in value or any(ord(char) < 32 for char in value):
            raise ValueError(f"identity.{key} contains a placeholder or control character")
    if not re.fullmatch(r"[A-Za-z0-9.-]{3,50}", identity["name"]):
        raise ValueError("identity.name must be the Package/Identity/Name, not a Store URL")
    if not identity["publisher"].startswith("CN="):
        raise ValueError("identity.publisher must be the exact CN=... from Partner Center")
    if not re.fullmatch(r"[1-9][0-9]*\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.0", version):
        raise ValueError("Store version must be Major.Minor.Build.0, with Major >= 1")
    if any(int(part) > 65535 for part in version.split(".")):
        raise ValueError("Store version components must be <= 65535")


def require_x64_pe(path: Path) -> None:
    with path.open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise ValueError(f"not a Windows executable: {path.name}")
        stream.seek(struct.unpack_from("<I", header, 60)[0])
        pe = stream.read(26)
    if len(pe) != 26 or pe[:4] != b"PE\0\0" or struct.unpack_from("<H", pe, 4)[0] != 0x8664:
        raise ValueError(f"expected an x64 executable, not an installer or another architecture: {path.name}")
    if struct.unpack_from("<H", pe, 24)[0] != 0x20B:
        raise ValueError(f"expected a PE32+ executable: {path.name}")


def validate_runtime(runtime: Path) -> None:
    # Require an extracted Fixed Version runtime, not its CAB or Evergreen setup EXE.
    require_x64_pe(runtime / "msedgewebview2.exe")
    for relative in ("icudtl.dat", "resources.pak", "locales/en-US.pak"):
        if not (runtime / relative).is_file():
            raise ValueError(f"incomplete Fixed Version WebView2 runtime: missing {relative}")
    for path in (runtime, *runtime.rglob("*")):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("WebView2 must not contain symlinks or directory junctions")


def manifest(identity: dict, version: str) -> str:
    validate_identity(identity, version)
    # win32App avoids virtualizing the AppData shared by GUI, CLI and external
    # harness hooks. Both restricted capabilities require Store review.
    return f'''<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
  xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
  xmlns:uap5="http://schemas.microsoft.com/appx/manifest/uap/windows10/5"
  xmlns:uap10="http://schemas.microsoft.com/appx/manifest/uap/windows10/10"
  xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
  IgnorableNamespaces="uap uap5 uap10 rescap">
  <Identity Name={quoteattr(identity['name'])} Publisher={quoteattr(identity['publisher'])}
    Version={quoteattr(version)} ProcessorArchitecture="x64" />
  <Properties>
    <DisplayName>Cutokyo App</DisplayName>
    <PublisherDisplayName>{escape(identity['publisher_display_name'])}</PublisherDisplayName>
    <Logo>Assets\\StoreLogo.png</Logo>
  </Properties>
  <Resources><Resource Language="en-US" /></Resources>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.19041.0" MaxVersionTested="10.0.26100.0" />
  </Dependencies>
  <Applications>
    <Application Id="Cutokyo" Executable="cutokyo-desktop.exe"
      uap10:RuntimeBehavior="win32App" uap10:TrustLevel="mediumIL">
      <uap:VisualElements DisplayName="Cutokyo App"
        Description="Local history and tools for AI coding sessions"
        Square150x150Logo="Assets\\Square150x150Logo.png"
        Square44x44Logo="Assets\\Square44x44Logo.png" BackgroundColor="transparent" />
    </Application>
    <Application Id="CutokyoCli" Executable="cutokyo.exe"
      uap10:RuntimeBehavior="win32App" uap10:TrustLevel="mediumIL"
      uap10:SupportsMultipleInstances="true" uap10:Subsystem="console">
      <uap:VisualElements DisplayName="Cutokyo CLI" AppListEntry="none"
        Description="Command line tools for Cutokyo"
        Square150x150Logo="Assets\\Square150x150Logo.png"
        Square44x44Logo="Assets\\Square44x44Logo.png" BackgroundColor="transparent" />
      <Extensions>
        <uap5:Extension Category="windows.appExecutionAlias">
          <uap5:AppExecutionAlias uap10:Subsystem="console">
            <uap5:ExecutionAlias Alias="cutokyo.exe" />
          </uap5:AppExecutionAlias>
        </uap5:Extension>
      </Extensions>
    </Application>
  </Applications>
  <Capabilities>
    <rescap:Capability Name="runFullTrust" />
    <rescap:Capability Name="unvirtualizedResources" />
  </Capabilities>
</Package>
'''


def stage(identity: dict, version: str, binaries: Path, runtime: Path, output: Path) -> None:
    xml = manifest(identity, version)
    for name in ("cutokyo-desktop.exe", "cutokyo.exe"):
        require_x64_pe(binaries / name)
    validate_runtime(runtime)
    icons = ROOT / "crates/cutokyo-desktop/icons"
    for name in ICONS:
        if not (icons / name).is_file():
            raise ValueError(f"missing Store asset: {name}")
    # Never delete/overwrite an arbitrary caller-supplied directory.
    output.mkdir(parents=True, exist_ok=False)
    for name in ("cutokyo-desktop.exe", "cutokyo.exe"):
        shutil.copy2(binaries / name, output / name)
    (output / "Assets").mkdir()
    for name in ICONS:
        shutil.copy2(icons / name, output / "Assets" / name)
    shutil.copytree(runtime, output / "WebView2")
    shutil.copy2(ROOT / "LICENSE", output / "LICENSE.txt")
    shutil.copy2(ROOT / "NOTICE", output / "NOTICE.txt")
    shutil.copy2(ROOT / "THIRD_PARTY.md", output / "THIRD_PARTY.md")
    (output / "AppxManifest.xml").write_text(xml, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--binaries", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    identity = json.loads(args.identity.read_text(encoding="utf-8-sig"))
    validate_identity(identity, args.version)
    validate_runtime(args.runtime)
    if args.validate_only:
        print("Store identity, package version and x64 WebView2 layout validated")
    else:
        if args.binaries is None or args.output is None:
            parser.error("staging requires --binaries and --output")
        stage(identity, args.version, args.binaries, args.runtime, args.output)
        print(f"MSIX layout staged: {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, TypeError) as error:
        print(f"MSIX refused: {error}", file=sys.stderr)
        raise SystemExit(78) from error
