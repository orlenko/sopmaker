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
$transaction = [guid]::NewGuid().ToString('N')
$setupLock = $null

function Assert-PayloadOnly([string]$Directory) {
    # A valid marker identifies the app, not ownership of arbitrary descendants.
    # Walk explicitly so no directory junction/symlink is followed or removed.
    if ((Get-Item -LiteralPath $Directory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Setup cannot replace a linked application folder. Use the normal installation location.'
    }
    $inventory = Get-Content -LiteralPath (Join-Path $Directory 'payload-sha256.json') -Raw | ConvertFrom-Json
    $files = @('payload-sha256.json', '.sopmaker-install.json') + @($inventory.PSObject.Properties.Name)
    $allowedFiles = @{}
    $allowedDirectories = @{}
    foreach ($name in $files) {
        $full = [IO.Path]::GetFullPath((Join-Path $Directory $name))
        if (-not $full.StartsWith($Directory + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'The installed file inventory is invalid. Nothing was replaced.'
        }
        $allowedFiles[$full] = $true
        $ancestor = Split-Path -Parent $full
        while ($ancestor -ne $Directory) {
            $allowedDirectories[$ancestor] = $true
            $ancestor = Split-Path -Parent $ancestor
        }
    }
    $pending = New-Object 'System.Collections.Generic.Stack[string]'
    $pending.Push($Directory)
    while ($pending.Count) {
        foreach ($item in Get-ChildItem -LiteralPath $pending.Pop() -Force) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'The application folder contains a link. Nothing was replaced; keep inspection data outside the app folder.'
            }
            if ($item.PSIsContainer) {
                if (-not $allowedDirectories.ContainsKey($item.FullName)) {
                    throw 'The application folder contains extra data. Nothing was replaced. Keep notebooks outside the app folder before updating.'
                }
                $pending.Push($item.FullName)
            } elseif (-not $allowedFiles.ContainsKey($item.FullName)) {
                throw 'The application folder contains extra files. Nothing was replaced. Keep notebooks outside the app folder before updating.'
            }
        }
    }
}

function Assert-AppClosed([string]$Directory) {
    foreach ($process in @(Get-Process -Name 'Sopmaker' -ErrorAction SilentlyContinue)) {
        if ($process.Path -and $process.Path.StartsWith($Directory + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Close the Sopmaker app window before installing an update. Do not close it during processing.'
        }
    }
}

try {
    if (-not [Environment]::Is64BitOperatingSystem -or
        ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64' -and $env:PROCESSOR_ARCHITEW6432 -ne 'AMD64')) {
        throw 'This package requires an x64 Windows computer.'
    }
    # One installer per Windows user, across targets, aliases and logon sessions.
    # FileShare.None is the ownership primitive; the persistent empty file is not.
    $lockDirectory = Join-Path $env:LOCALAPPDATA 'Sopmaker-Setup'
    New-Item -ItemType Directory -Force -Path $lockDirectory | Out-Null
    try {
        $setupLock = [IO.File]::Open((Join-Path $lockDirectory 'setup.lock'), [IO.FileMode]::OpenOrCreate,
                                    [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch {
        throw 'Another Sopmaker setup is running. Wait for it to finish, then try again.'
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
        Assert-AppClosed $InstallDir
        Assert-PayloadOnly $InstallDir
    }
    $parent = Split-Path -Parent $InstallDir
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $stage = Join-Path $parent ('Sopmaker-stage-' + $transaction)
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
    @{ application = 'Sopmaker'; transaction = $transaction; source_revision = (Get-Content -LiteralPath (Join-Path $stage 'build-info.json') -Raw | ConvertFrom-Json).source_revision } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $stage '.sopmaker-install.json') -Encoding UTF8
    if (Test-Path -LiteralPath $InstallDir) {
        # Recheck after the copy/hash work, immediately before committing.
        Assert-AppClosed $InstallDir
        Assert-PayloadOnly $InstallDir
        $backup = Join-Path $parent ('Sopmaker-backup-' + $transaction)
        [IO.Directory]::Move($InstallDir, $backup)
    }
    # Directory.Move refuses an existing destination; it cannot nest a stage.
    [IO.Directory]::Move($stage, $InstallDir)
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
    if ($backup) {
        # Retain the previous copy. Never recursively delete a committed root:
        # an app opened during setup could have written data after validation.
        Write-Host "Previous application copy retained at: $backup"
    }
    Write-Host "Installed: $InstallDir"
    Write-Host 'Next time, open the Sopmaker shortcut on your desktop.'
    Write-Host 'In the browser: Setup > enter your own API key > Source files > drop files > Generate draft report.'
    if (-not $NoLaunch) {
        try { Start-Process -FilePath (Join-Path $InstallDir 'Sopmaker.exe') -WorkingDirectory $InstallDir } catch {
            Write-Warning 'Installed successfully. Open the desktop shortcut to start Sopmaker.'
        }
    }
} catch {
    $failure = $_.Exception.Message
    if ($stage -and (Test-Path -LiteralPath $stage)) {
        try { Remove-Item -LiteralPath $stage -Recurse -Force } catch { Write-Warning "An unused setup copy remains at $stage." }
    }
    # Roll back only our transaction, preserving even unexpected late writes.
    if ($installed -and (Test-Path -LiteralPath $marker)) {
        try {
            $owner = Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
            if ($owner.transaction -ne $transaction) { throw 'Installation ownership changed.' }
            $recovery = Join-Path $parent ('Sopmaker-failed-' + $transaction)
            [IO.Directory]::Move($InstallDir, $recovery)
            Write-Warning "The incomplete new application copy was preserved at $recovery."
        } catch { Write-Warning 'Could not move the incomplete app aside. Existing copies have been preserved.' }
    }
    if ($backup -and (Test-Path -LiteralPath $backup)) {
        try { [IO.Directory]::Move($backup, $InstallDir) } catch { Write-Warning "The previous application is preserved at $backup." }
    }
    foreach ($link in $newShortcuts) { Remove-Item -LiteralPath $link -Force -ErrorAction SilentlyContinue }
    Write-Host ('Installation could not finish: ' + $failure) -ForegroundColor Red
    Write-Host 'Your inspection notebooks and saved API key have not been changed.'
    exit 1
} finally {
    if ($setupLock) { $setupLock.Dispose() }
}
