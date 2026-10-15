# Microsoft Store (MSIX)

This is a separate packaging path from the website's NSIS EXE and the signed
release workflow. It creates an **unsigned candidate**, not a certified release.
Microsoft signs MSIX packages distributed by the Store after certification. The
EXE/MSI submission form instead requires a publisher-signed installer URL.

## Identity and prerequisites

Use a Partner Center **MSIX** product. Copy these public values from its product
identity page into a JSON file matching `tools/release/msix-identity.example.json`:

| JSON key                 | Partner Center value                          |
| ------------------------ | --------------------------------------------- |
| `name`                   | `Package/Identity/Name`                       |
| `publisher`              | `Package/Identity/Publisher`, including `CN=` |
| `publisher_display_name` | `Package/Properties/PublisherDisplayName`     |

The registered **Cutokyo App** identity is already stored in
`tools/release/msix-identity.json` and used by default. Its package name is
`Cutokyo.CutokyoApp`, publisher is `CN=98AB1BDF-9FE8-4BCC-9536-D291343A66D7`,
and publisher display name is `Cutokyo`. These identifiers are public, not
credentials. Both the package and desktop Start menu entry use **Cutokyo App**,
matching the reserved Store name.

Do not substitute the website domain, GitHub username or Store product ID. Do not
delete an existing EXE/MSI product to try to convert it: confirm the available
registration/migration options in Partner Center first.

The Store package version is independent of Cutokyo's `0.x` application version.
Use an explicitly chosen, increasing `Major.Minor.Build.0` (for example `1.0.0.0`
for the first Store candidate); the first component must be positive and the last
is reserved for the Store. Do not change the application's version just to pack it.

Build on Windows x64 with the repository's pinned Rust, Node and pnpm toolchains,
Python 3.11+, Visual Studio C++ build tools and Windows SDK MakeAppx.exe. This
recipe targets Windows Desktop build 19041 or later; that manifest floor is **not
evidence that all Windows versions have been tested**. Only x64 is packaged.

Download an **x64 Fixed Version WebView2 runtime CAB** from Microsoft's WebView2
download page, verify its origin, record its SHA256 and extract it with:

```powershell
New-Item C:\CutokyoBuild\WebView2 -ItemType Directory
expand.exe -F:* C:\Downloads\Microsoft.WebView2.FixedVersionRuntime.x64.cab C:\CutokyoBuild\WebView2
```

Pass the extracted directory that directly contains `msedgewebview2.exe`, not
the CAB, an Evergreen installer, or a machine's installed Edge directory. Keep
the complete runtime, including its license notices. Fixed Version does not
automatically update: ship updated runtimes in subsequent Store packages.
The workflow pins the official download URL and SHA256 in
`tools/release/msix-webview2.json`. Update that file deliberately when upgrading
the runtime. The initial runtime's executable signature was verified as Microsoft
Corporation during local preparation.

## Build

From a Windows checkout on the feature branch, after putting pnpm on PATH:

```powershell
./tools/release/build-msix.ps1 `
  -PackageVersion 1.0.0.0 `
  -WebView2Directory C:\CutokyoBuild\WebView2\Microsoft.WebView2.FixedVersionRuntime.VERSION.x64
```

The script refuses an existing output directory. For a later attempt, choose a
new `-OutputDirectory`. It builds both frontends from the checkout with the static
MSVC CRT and the MSIX-only Tauri configuration. No Rust/application behavior is
changed. WebView2 is temporarily copied to an ignored directory for Tauri's build
and removed in `finally`; existing content there is never overwritten.

Outputs in `target/msix`:

- `Cutokyo-<package-version>-x64.msix` and its SHA256;
- `layout/AppxManifest.xml` and the packaged desktop, matching CLI, icons, license,
  third-party notices and offline WebView2 runtime;
- `build-report.json`: source revision/dirty status, versions, identity and hash.
  Installation and Store certification remain explicitly unverified.

The `MSIX candidate` workflow performs Windows boundary tests on pull requests.
Its manual dispatch builds an unsigned artifact using the registered identity,
the pinned runtime, and an explicit package-version input, without
Apple, npm or Authenticode credentials. GitHub normally exposes manual dispatch
only once a workflow exists on the default branch; until then, use the local
Windows command. Do not merge merely to make the dispatch button appear.

## Package behavior and certification notes

The package includes `cutokyo-desktop.exe` and the adjacent `cutokyo.exe` expected
by native capture and MCP discovery. It also declares the `cutokyo.exe` execution
alias. An existing CLI on PATH may win over that alias; test coexistence explicitly.

Both application entries remain visible in Start: **Cutokyo App** and
**Cutokyo CLI**. Do not hide the CLI with `AppListEntry="none"`: Partner Center
rejects that entry as a headless application unless the product has a
`HeadlessAppBypass` waiver. The visible CLI keeps its execution alias and console
activation behavior without requiring that waiver.

The manifest uses `win32App` with `mediumIL`, `runFullTrust` and
`unvirtualizedResources`. This is not administrator elevation. These restricted
capabilities require justification/review: Cutokyo must read user-selected local
agent histories, invoke local tools, and, after preview and consent, update agent
configuration with recovery copies. Unvirtualized access keeps AppData consistent
between the desktop, CLI and hooks launched by external tools. Removing these
capabilities just to pass submission would require a different, tested design.

Store updates own the package installation; this build does not enable the Tauri
signed updater. Do not claim that OS uninstall runs `cutokyo uninstall`: MSIX
removal does not run the application's cleanup command. Explain how to remove
Cutokyo-owned integrations before uninstall and how to retain/remove local data.

## Required validation before submission

Use an isolated Windows VM/test account, not the developer's real agent profile.
MakeAppx validation checks packaging, not application readiness or Store policy.

1. Sign a **copy** with a disposable test certificate matching the manifest's
   publisher; trust it only in the test VM. Keep the unsigned candidate for Store
   submission. Never commit a private key/PFX or install a test root on users' PCs.
2. Install as a standard user. Launch from Start, verify the actual GUI renders,
   and confirm the packaged Fixed Version runtime works without Evergreen.
3. Run the packaged CLI (`version`, `native-hook --help`) and compare its version
   with the desktop liveness probe. Neither probe alone tests the GUI.
4. With disposable agent fixtures, test browsing, capture preview/confirmation,
   search MCP, terminal resume, and shared GUI/CLI storage. Verify external tools
   see configuration changes and spool events, not a private virtualized copy.
5. Install a higher package version and check data retention and integrations.
   **Current integrations can record absolute paths to the adjacent CLI. MSIX
   versioned install paths may become stale after an update.** Test and resolve
   this before certifying; the execution alias alone does not rewrite those paths.
6. Remove owned integrations using the app/CLI, uninstall the package, and inspect
   remaining user data/configuration. Do not promise automatic hook cleanup.
7. Run the Windows App Certification Kit and resolve relevant findings. Then use
   Partner Center package validation, capability review and certification. Review
   privacy/support/contact details and the IARC online-content answer separately.

No MSIX installation, GUI, upgrade, integration or WACK result is implied by a
successful packaging test. Record actual results before describing a candidate as
ready for public distribution.

## References

- [Store signing and submission formats](https://learn.microsoft.com/en-us/windows/apps/publish/get-started)
- [MSIX package/version requirements](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/app-package-requirements)
- [Packaged desktop runtime behavior](https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-behind-the-scenes)
- [WebView2 distribution](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)
- [Tauri Windows packaging](https://v2.tauri.app/distribute/windows-installer/)
