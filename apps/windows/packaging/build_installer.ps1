[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$Iscc = "",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$packagingDir = $PSScriptRoot
$windowsDir = [System.IO.DirectoryInfo]::new(
    [System.IO.Path]::GetFullPath((Join-Path $packagingDir ".."))
)
$distDir = [System.IO.Path]::GetFullPath((Join-Path $packagingDir "dist"))
$workDir = [System.IO.Path]::GetFullPath((Join-Path $packagingDir "build"))
$outputDir = [System.IO.Path]::GetFullPath((Join-Path $packagingDir "output"))

foreach ($target in @($distDir, $workDir, $outputDir)) {
    if ([System.IO.Path]::GetDirectoryName($target) -ne $packagingDir) {
        throw "Refusing to clean packaging path outside $packagingDir`: $target"
    }
    if ([System.IO.Directory]::Exists($target)) {
        [System.IO.Directory]::Delete($target, $true)
    }
}

$version = & $Python -c "import tomllib; print(tomllib.load(open(r'$($windowsDir.FullName)\pyproject.toml','rb'))['project']['version'])"
if ($LASTEXITCODE -ne 0 -or -not $version) {
    throw "Unable to read the application version"
}
$numericVersion = & $Python -c "import re, tomllib; v=tomllib.load(open(r'$($windowsDir.FullName)\pyproject.toml','rb'))['project']['version']; nums=[int(x) for x in re.findall(r'\d+', v)]; print('.'.join(map(str, (nums + [0, 0, 0, 0])[:4])))"
if ($LASTEXITCODE -ne 0 -or -not $numericVersion) {
    throw "Unable to normalize the application version"
}

Push-Location $packagingDir
try {
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --distpath $distDir `
        --workpath $workDir `
        (Join-Path $packagingDir "yancuo.spec")
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }

    $executable = Join-Path $distDir "Yancuo\Yancuo.exe"
    if (-not [System.IO.File]::Exists($executable)) {
        throw "Packaged executable is missing: $executable"
    }

    if ($SkipInstaller) {
        Write-Host "Executable bundle: $executable"
        return
    }

    if (-not $Iscc) {
        $resolved = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
        if ($resolved) {
            $Iscc = $resolved.Source
        } else {
            $isccCandidates = @(
                (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
                (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
                (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
            )
            foreach ($candidate in $isccCandidates) {
                if ([System.IO.File]::Exists($candidate)) {
                    $Iscc = $candidate
                    break
                }
            }
        }
    }
    if (-not $Iscc -or -not [System.IO.File]::Exists($Iscc)) {
        throw "ISCC.exe not found. Install Inno Setup 6 or pass -Iscc <path>."
    }

    & $Iscc `
        "/DMyAppVersion=$version" `
        "/DMyVersionInfo=$numericVersion" `
        (Join-Path $packagingDir "yancuo.iss")
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed with exit code $LASTEXITCODE"
    }

    $installer = Get-ChildItem -LiteralPath $outputDir -Filter "*-setup.exe" |
        Select-Object -First 1
    if (-not $installer) {
        throw "Installer output was not created"
    }
    $hash = Get-FileHash -LiteralPath $installer.FullName -Algorithm SHA256
    Write-Host "Installer: $($installer.FullName)"
    Write-Host "SHA256: $($hash.Hash)"
}
finally {
    Pop-Location
}
