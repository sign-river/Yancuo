[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Installer,
    [string]$WorkRoot = (Join-Path $env:TEMP "yancuo-installer-verification")
)

$ErrorActionPreference = "Stop"
$installerPath = [System.IO.Path]::GetFullPath($Installer)
if (-not [System.IO.File]::Exists($installerPath)) {
    throw "Installer not found: $installerPath"
}

$workRootPath = [System.IO.Path]::GetFullPath($WorkRoot)
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd("\")
if ([System.IO.Path]::GetDirectoryName($workRootPath) -ne $tempRoot) {
    throw "WorkRoot must be a direct child of the system temporary directory"
}
if ([System.IO.Directory]::Exists($workRootPath)) {
    [System.IO.Directory]::Delete($workRootPath, $true)
}
[System.IO.Directory]::CreateDirectory($workRootPath) | Out-Null
[System.IO.File]::WriteAllText(
    (Join-Path $workRootPath ".yancuo-installer-verification"),
    "installer verification only"
)

$installDir = Join-Path $workRootPath "application"
$profileLocal = Join-Path $workRootPath "profile-local"
$dataRoot = Join-Path $profileLocal "Yancuo"
$smokeReport = Join-Path $workRootPath "smoke.json"
$sentinel = Join-Path $dataRoot "upgrade-uninstall-sentinel.txt"
$uninstaller = Join-Path $installDir "unins000.exe"

function Invoke-Installer {
    $process = Start-Process -FilePath $installerPath -ArgumentList @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/DIR=$installDir"
    ) -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0) {
        throw "Installer failed with exit code $($process.ExitCode)"
    }
}

try {
    Invoke-Installer
    $executable = Join-Path $installDir "Yancuo.exe"
    if (-not [System.IO.File]::Exists($executable)) {
        throw "Installed executable is missing"
    }

    $originalLocalAppData = $env:LOCALAPPDATA
    $env:LOCALAPPDATA = $profileLocal
    Remove-Item Env:YANCUO_DATA_ROOT -ErrorAction SilentlyContinue
    $env:YANCUO_PACKAGING_SMOKE_REPORT = $smokeReport
    $smoke = Start-Process -FilePath $executable `
        -ArgumentList "--packaging-smoke-test" `
        -Wait -PassThru -WindowStyle Hidden
    if ($smoke.ExitCode -ne 0 -or -not [System.IO.File]::Exists($smokeReport)) {
        throw "Installed application smoke test failed"
    }
    $report = Get-Content -LiteralPath $smokeReport -Raw -Encoding UTF8 |
        ConvertFrom-Json
    if (-not $report.passed -or $report.data_root -ne $dataRoot) {
        throw "Installed resources or isolated data root failed verification"
    }

    [System.IO.File]::WriteAllText($sentinel, "must survive upgrade and uninstall")
    Invoke-Installer
    if (-not [System.IO.File]::Exists($sentinel)) {
        throw "Upgrade removed user data"
    }

    if (-not [System.IO.File]::Exists($uninstaller)) {
        throw "Uninstaller is missing"
    }
    $uninstall = Start-Process -FilePath $uninstaller -ArgumentList @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART"
    ) -Wait -PassThru -WindowStyle Hidden
    if ($uninstall.ExitCode -ne 0) {
        throw "Uninstaller failed with exit code $($uninstall.ExitCode)"
    }
    if (-not [System.IO.File]::Exists($sentinel)) {
        throw "Uninstall removed user data"
    }
    if ([System.IO.File]::Exists($executable)) {
        throw "Uninstall left the application executable behind"
    }
    Write-Host "Installer verification passed; user data survived upgrade and uninstall."
}
finally {
    Remove-Item Env:YANCUO_DATA_ROOT -ErrorAction SilentlyContinue
    Remove-Item Env:YANCUO_PACKAGING_SMOKE_REPORT -ErrorAction SilentlyContinue
    if ($null -ne $originalLocalAppData) {
        $env:LOCALAPPDATA = $originalLocalAppData
    }
    if ([System.IO.Directory]::Exists($workRootPath)) {
        [System.IO.Directory]::Delete($workRootPath, $true)
    }
}
