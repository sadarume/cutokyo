# Builds an unsigned Store submission; never installs certificates or publishes.
[CmdletBinding()]
param(
    [string]$IdentityFile,
    [Parameter(Mandatory = $true)][string]$PackageVersion,
    [Parameter(Mandatory = $true)][string]$WebView2Directory,
    [string]$OutputDirectory,
    [string]$MakeAppx
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}

$repository = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
if (-not $IdentityFile) { $IdentityFile = Join-Path $PSScriptRoot 'msix-identity.json' }
$identity = (Resolve-Path $IdentityFile).Path
$runtime = (Resolve-Path $WebView2Directory).Path
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repository 'target/msix' }
$output = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path $output) { throw 'OutputDirectory already exists. Choose a new directory.' }
if (-not $MakeAppx) {
    $sdkRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits/10/bin'
    $candidates = @(Get-ChildItem $sdkRoot -Directory | Where-Object { $_.Name -match '^10\.0\.\d+\.0$' } |
        Sort-Object { [version]$_.Name } -Descending |
        ForEach-Object { Join-Path $_.FullName 'x64/makeappx.exe' } | Where-Object { Test-Path $_ })
    if ($candidates.Count -eq 0) { throw 'Install the Windows SDK (MakeAppx.exe) first.' }
    $MakeAppx = $candidates[0]
}
$MakeAppx = (Resolve-Path $MakeAppx).Path
foreach ($program in @('python', 'cargo', 'pnpm')) { Get-Command $program -ErrorAction Stop | Out-Null }
if ($env:CARGO_ENCODED_RUSTFLAGS) { throw 'Unset CARGO_ENCODED_RUSTFLAGS so the static CRT build flags take effect.' }
$runtimeBuild = Join-Path $repository 'crates/cutokyo-desktop/WebView2'
if (Test-Path $runtimeBuild) { throw 'Remove or relocate the existing desktop/WebView2 directory first.' }
$oldRustFlags = $env:RUSTFLAGS
$oldTargetDir = $env:CARGO_TARGET_DIR
$copiedRuntime = $false
Push-Location $repository
try {
    Invoke-Checked python @('tools/release/msix.py', '--identity', $identity, '--version', $PackageVersion,
        '--runtime', $runtime, '--validate-only')
    New-Item $output -ItemType Directory | Out-Null
    New-Item $runtimeBuild -ItemType Directory | Out-Null
    $copiedRuntime = $true
    Get-ChildItem $runtime -Force | Copy-Item -Destination $runtimeBuild -Recurse
    # Both frontends are self-contained with respect to the MSVC CRT. Use an
    # isolated target directory so website/release artifacts are not reused.
    $env:RUSTFLAGS = '-C target-feature=+crt-static'
    $env:CARGO_TARGET_DIR = Join-Path $output 'build'
    Invoke-Checked pnpm @('install', '--frozen-lockfile')
    Invoke-Checked pnpm @('exec', 'tauri', 'build', '--ci', '--no-bundle', '--target', 'x86_64-pc-windows-msvc',
        '--config', (Join-Path $repository 'crates/cutokyo-desktop/tauri.msix.conf.json'), '--', '--locked')
    Invoke-Checked cargo @('build', '--release', '--locked', '--target', 'x86_64-pc-windows-msvc', '-p', 'cutokyo-cli')
    $binaries = Join-Path $env:CARGO_TARGET_DIR 'x86_64-pc-windows-msvc/release'
    $expectedVersion = (Get-Content 'crates/cutokyo-desktop/tauri.conf.json' -Raw | ConvertFrom-Json).version
    $desktopProbe = Invoke-Checked (Join-Path $binaries 'cutokyo-desktop.exe') @('--cutokyo-probe-liveness')
    $desktopVersion = ($desktopProbe | ConvertFrom-Json).app_version
    $cliProbe = Invoke-Checked (Join-Path $binaries 'cutokyo.exe') @('--data-dir', (Join-Path $output 'probe-data'),
        '--config-file', (Join-Path $output 'probe-config.toml'), 'version')
    if ($desktopVersion -ne $expectedVersion -or ($cliProbe -join "`n").Trim() -ne "cutokyo $expectedVersion") {
        throw 'Desktop and CLI probes must report the expected application version.'
    }
    Invoke-Checked (Join-Path $binaries 'cutokyo.exe') @('native-hook', '--help')
    $layout = Join-Path $output 'layout'
    Invoke-Checked python @('tools/release/msix.py', '--identity', $identity, '--version', $PackageVersion,
        '--runtime', $runtimeBuild, '--binaries', $binaries, '--output', $layout)
    $package = Join-Path $output "Cutokyo-$PackageVersion-x64.msix"
    # Keep MakeAppx schema/semantic validation enabled (no /nv).
    Invoke-Checked $MakeAppx @('pack', '/d', $layout, '/p', $package, '/o')
    $hash = (Get-FileHash $package -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $([IO.Path]::GetFileName($package))" | Set-Content "$package.sha256" -Encoding ASCII
    $commit = (& git rev-parse HEAD)
    if ($LASTEXITCODE -ne 0) { throw 'Could not record source revision.' }
    $changes = @(& git status --porcelain)
    if ($LASTEXITCODE -ne 0) { throw 'Could not record source status.' }
    @{
        source_commit = $commit
        source_dirty = ($changes.Count -gt 0)
        package_version = $PackageVersion
        app_version = $expectedVersion
        webview2_version = (Get-Item (Join-Path $runtimeBuild 'msedgewebview2.exe')).VersionInfo.FileVersion
        identity = (Get-Content $identity -Raw | ConvertFrom-Json)
        sha256 = $hash
        makeappx_validation = 'passed'
        binary_probes = 'passed'
        installed_and_launched = $false
        store_certified = $false
    } | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $output 'build-report.json') -Encoding UTF8
    Write-Host "Unsigned Store candidate: $package"
    Write-Host 'Installation, GUI/integration/upgrade tests and Store certification are still required.'
} finally {
    $env:RUSTFLAGS = $oldRustFlags
    $env:CARGO_TARGET_DIR = $oldTargetDir
    if ($copiedRuntime) { Remove-Item $runtimeBuild -Recurse -Force }
    Pop-Location
}
