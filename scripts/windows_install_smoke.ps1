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
    Assert-True (@(Get-ChildItem -Path (Split-Path -Parent $install) -Filter 'Sopmaker-backup-*').Count -eq 0) 'Old application backup was not cleaned up'
    Write-Host 'Windows installation, shortcut, installed-binary, running-app refusal and update checks passed.'
} finally {
    if (Test-Path -LiteralPath $root) { Remove-Item -LiteralPath $root -Recurse -Force }
}
