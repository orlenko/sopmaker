# Runs only on Windows, using disposable destinations and an already-built app.
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$SetupDir)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$SetupDir = (Resolve-Path -LiteralPath $SetupDir).Path
$root = Join-Path ([IO.Path]::GetTempPath()) ('Sopmaker install test ' + [guid]::NewGuid().ToString('N'))
$install = Join-Path $root 'Apps with spaces\Sopmaker'
$desktop = Join-Path $root 'Desktop'
$menu = Join-Path $root 'Start menu'
$arguments = @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $SetupDir 'Install-Sopmaker.ps1'),
               '-InstallDir', $install, '-DesktopDir', $desktop, '-StartMenuDir', $menu, '-NoLaunch')

function Assert-True($condition, $message) {
    if (-not $condition) { throw $message }
}

try {
    New-Item -ItemType Directory -Path $root | Out-Null
    $data = Join-Path $root 'Documents\Sopmaker\Notebook'
    New-Item -ItemType Directory -Path $data -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $data 'preserve.txt') -Value 'Invented inspection data'
    # Refuse to replace unrelated folders, even an empty directory.
    New-Item -ItemType Directory -Path $install -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $install 'keep.txt') -Value 'Unrelated file'
    & powershell.exe @arguments
    Assert-True ($LASTEXITCODE -eq 1) 'Installer replaced an unrelated destination'
    Assert-True (Test-Path -LiteralPath (Join-Path $install 'keep.txt')) 'Unrelated data changed'
    Remove-Item -LiteralPath $install -Recurse -Force

    & powershell.exe @arguments
    Assert-True ($LASTEXITCODE -eq 0) 'First install failed'
    Assert-True (Test-Path -LiteralPath (Join-Path $install '.sopmaker-install.json')) 'Install marker missing'
    $shell = New-Object -ComObject WScript.Shell
    foreach ($folder in @($desktop, $menu)) {
        $link = $shell.CreateShortcut((Join-Path $folder 'Sopmaker.lnk'))
        Assert-True ($link.TargetPath -eq (Join-Path $install 'Sopmaker.exe')) 'Shortcut target incorrect'
        Assert-True ($link.WorkingDirectory -eq $install) 'Shortcut working directory incorrect'
    }
    # Exercise the installed binary, including Unicode paths, server and DOCX export.
    & uv run python scripts/packaged_smoke.py (Join-Path $install 'Sopmaker.exe')
    Assert-True ($LASTEXITCODE -eq 0) 'Installed executable smoke failed'

    # An otherwise valid installation marker does not authorize deleting a notebook.
    $inside = Join-Path $install 'Nested Notebook'
    New-Item -ItemType Directory -Path $inside | Out-Null
    $sentinel = Join-Path $inside 'inspection.sqlite3'
    Set-Content -LiteralPath $sentinel -Value 'Invented notebook bytes must survive'
    $originalHash = (Get-FileHash -LiteralPath (Join-Path $install 'Sopmaker.exe')).Hash
    & powershell.exe @arguments
    Assert-True ($LASTEXITCODE -eq 1) 'Update accepted unowned notebook data inside the app root'
    Assert-True ((Get-Content -LiteralPath $sentinel).Trim() -eq 'Invented notebook bytes must survive') 'Nested notebook changed'
    Assert-True ((Get-FileHash -LiteralPath (Join-Path $install 'Sopmaker.exe')).Hash -eq $originalHash) 'Refused update changed the app'
    Remove-Item -LiteralPath $inside -Recurse -Force

    # Reparse entries must be refused without following or deleting their target.
    $junction = Join-Path $install 'Linked notebook'
    New-Item -ItemType Junction -Path $junction -Target $data | Out-Null
    try {
        & powershell.exe @arguments
        Assert-True ($LASTEXITCODE -eq 1) 'Update accepted a directory junction'
        Assert-True (Test-Path -LiteralPath (Join-Path $data 'preserve.txt')) 'Linked data changed'
    } finally { [IO.Directory]::Delete($junction) }

    # An app in use must be left running and unchanged, rather than killed by setup.
    $log = Join-Path $root 'running.log'
    $app = Start-Process -FilePath (Join-Path $install 'Sopmaker.exe') -ArgumentList @('serve', '--workspace', ('"' + (Join-Path $root 'Live notebook') + '"'), '--no-open') -PassThru -RedirectStandardOutput $log
    try {
        $ready = $false
        for ($attempt = 0; $attempt -lt 100; $attempt++) {
            if ((Test-Path -LiteralPath $log) -and (Get-Content -LiteralPath $log -Raw) -match 'Open: ') { $ready = $true; break }
            Start-Sleep -Milliseconds 200
        }
        Assert-True $ready 'Installed app did not start'
        & powershell.exe @arguments
        Assert-True ($LASTEXITCODE -eq 1) 'Installer did not refuse a running app'
        $app.Refresh()
        Assert-True (-not $app.HasExited) 'Installer stopped the running app'
    } finally {
        if (-not $app.HasExited) { Stop-Process -Id $app.Id -Force; $app.WaitForExit() }
    }
    & powershell.exe @arguments
    Assert-True ($LASTEXITCODE -eq 0) 'Reinstall/update failed'
    Assert-True ((Get-Content -LiteralPath (Join-Path $data 'preserve.txt')).Trim() -eq 'Invented inspection data') 'Notebook changed during update'
    Assert-True (@(Get-ChildItem -Path (Split-Path -Parent $install) -Filter 'Sopmaker-backup-*').Count -eq 1) 'Previous application copy was not preserved'

    # Deterministically pause one REAL installer before its commit while it owns
    # the global per-user lock. A competing installer must refuse without writes.
    # Force a late shortcut failure in the first, then verify its own rollback.
    $readyName = 'Local\SopmakerReady-' + [guid]::NewGuid().ToString('N')
    $releaseName = 'Local\SopmakerRelease-' + [guid]::NewGuid().ToString('N')
    $ready = New-Object -TypeName Threading.EventWaitHandle -ArgumentList @($false, [Threading.EventResetMode]::ManualReset, $readyName)
    $release = New-Object -TypeName Threading.EventWaitHandle -ArgumentList @($false, [Threading.EventResetMode]::ManualReset, $releaseName)
    $driver = Join-Path $root 'paused-installer.ps1'
    @'
param($Installer, $Destination, $BlockedDesktop, $Menu, $ReadyName, $ReleaseName)
$ready = [Threading.EventWaitHandle]::OpenExisting($ReadyName)
$release = [Threading.EventWaitHandle]::OpenExisting($ReleaseName)
$line = (Select-String -LiteralPath $Installer -Pattern '^\s+Assert-AppClosed \$InstallDir' | Select-Object -Last 1).LineNumber
Set-PSBreakpoint -Script $Installer -Line $line -Action {
    $ready.Set() | Out-Null
    if (-not $release.WaitOne(60000)) { throw 'Test barrier timed out' }
} | Out-Null
$global:LASTEXITCODE = 0
& $Installer -InstallDir $Destination -DesktopDir $BlockedDesktop -StartMenuDir $Menu -NoLaunch
exit $LASTEXITCODE
'@ | Set-Content -LiteralPath $driver -Encoding UTF8
    $blockedDesktop = Join-Path $root 'This is a file.txt'
    Set-Content -LiteralPath $blockedDesktop -Value 'Cannot create a shortcut directory here'
    $beforeTransaction = (Get-Content -LiteralPath (Join-Path $install '.sopmaker-install.json') -Raw | ConvertFrom-Json).transaction
    $owner = Start-Process powershell.exe -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $driver + '"'),
        ('"' + (Join-Path $SetupDir 'Install-Sopmaker.ps1') + '"'), ('"' + $install + '"'), ('"' + $blockedDesktop + '"'),
        ('"' + $menu + '"'), $readyName, $releaseName) -PassThru -RedirectStandardOutput (Join-Path $root 'paused.log') -RedirectStandardError (Join-Path $root 'paused-errors.log')
    try {
        Assert-True ($ready.WaitOne(60000)) 'Owner did not reach the test barrier'
        & powershell.exe @arguments
        Assert-True ($LASTEXITCODE -eq 1) 'Concurrent setup was not refused'
        Assert-True ((Get-Content -LiteralPath (Join-Path $install '.sopmaker-install.json') -Raw | ConvertFrom-Json).transaction -eq $beforeTransaction) 'Competing setup changed the original app'
        $release.Set() | Out-Null
        Assert-True ($owner.WaitForExit(60000)) 'Paused owner failed to exit'
        Assert-True ($owner.ExitCode -eq 1) 'Late shortcut failure was not reported'
        Assert-True ((Get-Content -LiteralPath (Join-Path $install '.sopmaker-install.json') -Raw | ConvertFrom-Json).transaction -eq $beforeTransaction) 'Rollback did not restore the original app'
        Assert-True ((Get-FileHash -LiteralPath (Join-Path $install 'Sopmaker.exe')).Hash -eq $originalHash) 'Rollback changed the executable'
        Assert-True (@(Get-ChildItem -Path (Split-Path -Parent $install) -Filter 'Sopmaker-failed-*').Count -eq 1) 'Incomplete transaction copy was not preserved'
    } finally {
        $release.Set() | Out-Null
        $owner.Refresh()
        if (-not $owner.HasExited) { Stop-Process -Id $owner.Id -Force; $owner.WaitForExit() }
        $ready.Dispose()
        $release.Dispose()
    }
    Write-Host 'Windows installation, shortcut, installed-binary, nested-data/reparse protection, running-app refusal, serialized setup, rollback and update checks passed.'
} finally {
    if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force }
}
