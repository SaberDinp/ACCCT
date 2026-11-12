# Run only on a disposable native Windows Actions runner, without elevation.
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or -not $IsWindows) {
    throw 'This installation check requires a disposable Windows GitHub Actions runner.'
}
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$manifest = Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'desktop/package.json') | ConvertFrom-Json
$installer = Join-Path $projectRoot "dist/ACCC-Telemetry-$($manifest.version)-win-x64.exe"
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) { throw "Missing installer: $installer" }

# /S suppresses the one-click installer's runAfterFinish action. Keep any
# unexpected child launch isolated from the runner user's ordinary app profile.
$isolation = Join-Path $env:RUNNER_TEMP 'accc-installer-check'
$previousUserData, $previousDataRoot = $env:ACCC_DESKTOP_USER_DATA, $env:ACCC_DATA_ROOT
try {
    $env:ACCC_DESKTOP_USER_DATA = Join-Path $isolation 'preferences'
    $env:ACCC_DATA_ROOT = Join-Path $isolation 'library'
    $process = Start-Process -FilePath $installer -ArgumentList '/S' -PassThru
    if (-not $process.WaitForExit(300000)) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        throw 'Silent installation exceeded five minutes.'
    }
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) { throw "Installer exited with code $($process.ExitCode)." }
} finally {
    $env:ACCC_DESKTOP_USER_DATA = $previousUserData
    $env:ACCC_DATA_ROOT = $previousDataRoot
}

# NSIS uses its app GUID for both these registry keys. Reading InstallLocation
# supports the actual per-user known-folder location instead of assuming C:.
$registrations = @(Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*' |
    Where-Object { $_.DisplayName -in @($manifest.productName, "$($manifest.productName) $($manifest.version)") -and
        $_.DisplayVersion -eq $manifest.version })
if ($registrations.Count -ne 1) { throw 'Expected one matching per-user uninstall registration.' }
$registration = $registrations[0]
$installKey = "HKCU:\Software\$($registration.PSChildName)"
$installDirectory = (Get-ItemProperty -LiteralPath $installKey).InstallLocation
if (-not $installDirectory) { throw 'Installer did not record InstallLocation.' }
if (Test-Path "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$($registration.PSChildName)") {
    throw 'Expected a per-user installation, but found a machine-wide registration.'
}
$executable = [IO.Path]::GetFullPath((Join-Path $installDirectory "$($manifest.productName).exe"))
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { throw "Installed executable missing: $executable" }
$version = [Diagnostics.FileVersionInfo]::GetVersionInfo($executable)
if ($version.ProductName -ne $manifest.productName -or
    ($version.ProductVersion -ne $manifest.version -and -not $version.ProductVersion.StartsWith("$($manifest.version)."))) {
    throw 'Installed executable branding or version does not match the package.'
}
$running = @(Get-Process -Name $manifest.productName -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq $executable })
if ($running.Count) {
    $running | Stop-Process -Force
    throw 'Silent installation unexpectedly launched the application.'
}

$shell = New-Object -ComObject WScript.Shell
$shortcuts = @{}
foreach ($folder in @('Programs', 'DesktopDirectory')) {
    $link = Join-Path ([Environment]::GetFolderPath($folder)) "$($manifest.build.nsis.shortcutName).lnk"
    if (-not (Test-Path -LiteralPath $link -PathType Leaf)) { throw "Shortcut missing: $link" }
    $target = [IO.Path]::GetFullPath($shell.CreateShortcut($link).TargetPath)
    if (-not $target.Equals($executable, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Shortcut points at the wrong executable: $link"
    }
    $shortcuts[$folder] = $link
}
$backend = Join-Path $installDirectory 'resources/backend'
$reportDirectory = Join-Path $projectRoot 'artifacts/desktop-package'
New-Item -ItemType Directory -Force -Path $reportDirectory | Out-Null
@{ installer = $installer; version = $manifest.version; exit_code = $process.ExitCode;
    scope = 'current user'; executable = $executable; shortcuts = $shortcuts; silent_autorun = $false } |
    ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 -LiteralPath (Join-Path $reportDirectory 'windows-installer.json')
Add-Content -Encoding utf8 -LiteralPath $env:GITHUB_ENV -Value @(
    "ACCC_INSTALLED_EXECUTABLE=$executable", "ACCC_INSTALLED_BACKEND_ROOT=$backend")
Write-Host "Installed $($manifest.productName) $($manifest.version) with valid Start menu and Desktop shortcuts."
