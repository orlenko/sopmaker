# Installs the adjacent, already-built application. No downloads, admin rights or API calls.
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\Sopmaker'),
    [string]$DesktopDir = [Environment]::GetFolderPath('Desktop'),
    [string]$StartMenuDir = (Join-Path ([Environment]::GetFolderPath('Programs')) 'Sopmaker'),
    [switch]$NoLaunch
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$stage = $null
$backup = $null
$installed = $false
$newShortcuts = @()

try {
    if (-not [Environment]::Is64BitOperatingSystem -or
        ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64' -and $env:PROCESSOR_ARCHITEW6432 -ne 'AMD64')) {
        throw 'This package requires an x64 Windows computer.'
    }
    $source = Join-Path $PSScriptRoot 'Sopmaker'
    foreach ($required in @('Sopmaker.exe', '_internal', 'build-info.json', 'LICENSE', 'payload-sha256.json')) {
        if (-not (Test-Path -LiteralPath (Join-Path $source $required))) {
            throw 'Extract the entire setup ZIP first, then run Install Sopmaker.cmd from the extracted folder.'
        }
    }
    $InstallDir = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
    $source = [IO.Path]::GetFullPath($source).TrimEnd('\')
    if ($source -eq $InstallDir -or $source.StartsWith($InstallDir + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $InstallDir.StartsWith($source + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Choose an installation folder outside the extracted setup package.'
    }
    $marker = Join-Path $InstallDir '.sopmaker-install.json'
    if (Test-Path -LiteralPath $InstallDir) {
        if (-not (Test-Path -LiteralPath $marker) -or
            (Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json).application -ne 'Sopmaker') {
            throw 'The destination already exists and is not a Sopmaker installation. Nothing was replaced.'
        }
        foreach ($process in @(Get-Process -Name 'Sopmaker' -ErrorAction SilentlyContinue)) {
            if ($process.Path -and $process.Path.StartsWith($InstallDir + '\', [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Close the Sopmaker app window before installing an update. Do not close it during processing.'
            }
        }
    }
    $parent = Split-Path -Parent $InstallDir
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $stage = Join-Path $parent ('Sopmaker-stage-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage | Out-Null
    Write-Host 'Installing Sopmaker for your Windows account...'
    foreach ($item in Get-ChildItem -LiteralPath $source -Force) {
        Copy-Item -LiteralPath $item.FullName -Destination $stage -Recurse -Force
    }
    # Detect incomplete/corrupt extraction or copying. This is not a code signature.
    $manifest = Get-Content -LiteralPath (Join-Path $stage 'payload-sha256.json') -Raw | ConvertFrom-Json
    if (@($manifest.PSObject.Properties).Count -eq 0 -or
        'Sopmaker.exe' -notin $manifest.PSObject.Properties.Name) {
        throw 'The application file manifest is missing or invalid. Extract a fresh setup package.'
    }
    foreach ($entry in $manifest.PSObject.Properties) {
        $file = [IO.Path]::GetFullPath((Join-Path $stage $entry.Name))
        if (-not $file.StartsWith($stage + '\', [StringComparison]::OrdinalIgnoreCase) -or
            -not (Test-Path -LiteralPath $file -PathType Leaf) -or
            (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash -ne $entry.Value) {
            throw 'The extracted application is incomplete or damaged. Extract a fresh copy of the setup ZIP.'
        }
    }
    @{ application = 'Sopmaker'; source_revision = (Get-Content -LiteralPath (Join-Path $stage 'build-info.json') -Raw | ConvertFrom-Json).source_revision } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $stage '.sopmaker-install.json') -Encoding UTF8
    if (Test-Path -LiteralPath $InstallDir) {
        $backup = Join-Path $parent ('Sopmaker-backup-' + [guid]::NewGuid().ToString('N'))
        Move-Item -LiteralPath $InstallDir -Destination $backup
    }
    Move-Item -LiteralPath $stage -Destination $InstallDir
    $stage = $null
    $installed = $true
    $shell = New-Object -ComObject WScript.Shell
    foreach ($folder in @($DesktopDir, $StartMenuDir)) {
        New-Item -ItemType Directory -Force -Path $folder | Out-Null
        $link = Join-Path $folder 'Sopmaker.lnk'
        if (-not (Test-Path -LiteralPath $link)) { $newShortcuts += $link }
        $shortcut = $shell.CreateShortcut($link)
        $shortcut.TargetPath = Join-Path $InstallDir 'Sopmaker.exe'
        $shortcut.WorkingDirectory = $InstallDir
        $shortcut.Description = 'Drop inspection files and generate a draft report'
        $shortcut.Save()
    }
} catch {
    if ($stage -and (Test-Path -LiteralPath $stage)) { Remove-Item -LiteralPath $stage -Recurse -Force }
    if ($installed) { Remove-Item -LiteralPath $InstallDir -Recurse -Force }
    if ($backup -and (Test-Path -LiteralPath $backup)) { Move-Item -LiteralPath $backup -Destination $InstallDir }
    foreach ($link in $newShortcuts) { Remove-Item -LiteralPath $link -Force -ErrorAction SilentlyContinue }
    Write-Host ('Installation could not finish: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Your inspection notebooks and saved API key have not been changed.'
    exit 1
}

if ($backup) {
    try { Remove-Item -LiteralPath $backup -Recurse -Force } catch {
        Write-Warning "Installed successfully; an old application copy remains at $backup."
    }
}
Write-Host "Installed: $InstallDir"
Write-Host 'Next time, open the Sopmaker shortcut on your desktop.'
Write-Host 'In the browser: Setup > enter your own API key > Source files > drop files > Generate draft report.'
if (-not $NoLaunch) {
    try { Start-Process -FilePath (Join-Path $InstallDir 'Sopmaker.exe') -WorkingDirectory $InstallDir } catch {
        Write-Warning 'Installed successfully. Open the desktop shortcut to start Sopmaker.'
    }
}
