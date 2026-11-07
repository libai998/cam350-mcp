#requires -Version 5.1
<#
.SYNOPSIS
    Install the CAM350 MCP server into a per-user virtual environment.

.DESCRIPTION
    Installs cam350-mcp-*.whl, runs a health check, and optionally registers
    the server with Claude Code.  No administrator rights are required.

.PARAMETER Wheel
    Path to cam350_mcp-0.1.0-py3-none-any.whl.  Defaults to the newest wheel
    in ..\dist.

.PARAMETER Python
    Python 3.10+ interpreter.  Auto-detected via "py -3" or "python".

.PARAMETER InstallDir
    Where the venv lives.  Default: %LOCALAPPDATA%\Programs\cam350-mcp.

.PARAMETER Register
    Register the server with Claude Code (removes a previous "cam350" entry).

.PARAMETER Client
    MCP client executable used by -Register: claude (default) or free-code.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Register
#>
[CmdletBinding()]
param(
    [string]$Wheel,
    [string]$Python,
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\cam350-mcp"),
    [switch]$Register,
    [ValidateSet("claude", "free-code")]
    [string]$Client = "claude"
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

# ---------------------------------------------------------------- wheel
if (-not $Wheel) {
    $root = Split-Path -Parent $PSScriptRoot
    $dist = Join-Path $root "dist"
    $candidate = Get-ChildItem -Path $dist -Filter "cam350_mcp-*.whl" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $candidate) {
        throw "Wheel not found under $dist. Build it with: py -3 -m pip wheel . -w dist --no-deps"
    }
    $Wheel = $candidate.FullName
}
if (-not (Test-Path $Wheel)) { throw "Wheel not found: $Wheel" }
Write-Step "Wheel: $Wheel"

# ---------------------------------------------------------------- python
function Get-PythonVersion([string]$Path) {
    try {
        $v = & $Path -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v -match '^(\d+)\.(\d+)$') {
            return [version]$v
        }
    }
    catch { }
    return $null
}

function Test-Python310([string]$Path) {
    $v = Get-PythonVersion $Path
    return ($v -ne $null -and $v -ge [version]'3.10')
}

if (-not $Python) {
    $candidates = @()
    # The Microsoft Store alias is a stub that fails to run: never use it.
    $stub = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\python.exe"
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try { $candidates += (& py -3 -c "import sys; print(sys.executable)").Trim() } catch { }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
    $globs = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python3*\python.exe"),
        "C:\Python3*\python.exe",
        "C:\Program Files\Python3*\python.exe",
        "C:\Program Files (x86)\Python3*\python.exe"
    )
    foreach ($g in $globs) { $candidates += (Get-ChildItem $g -ErrorAction SilentlyContinue | ForEach-Object FullName) }

    foreach ($c in $candidates) {
        if (-not $c) { continue }
        if ($c -eq $stub) { continue }
        if (-not (Test-Path $c)) { continue }
        if (Test-Python310 $c) { $Python = $c; break }
    }
}
if (-not $Python -or -not (Test-Path $Python)) {
    throw "Python 3.10+ not found. Install it from python.org or pass -Python <path>."
}
if (-not (Test-Python310 $Python)) {
    throw "Python 3.10 or newer is required: $Python"
}
Write-Step "Python: $Python"

# ---------------------------------------------------------------- venv
$venv = Join-Path $InstallDir "venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Step "Creating virtual environment: $venv"
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    & $Python -m venv $venv
}
Write-Step "Installing package"
# First pass installs dependencies; the second forces the wheel itself to be
# replaced even when the version number is unchanged (dev rebuilds).
& $venvPython -m pip install --upgrade --quiet $Wheel
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
& $venvPython -m pip install --upgrade --no-deps --force-reinstall --quiet $Wheel
if ($LASTEXITCODE -ne 0) { throw "pip reinstall failed" }

$serverExe = Join-Path $venv "Scripts\cam350-mcp.exe"
Write-Step "Health check"
& $serverExe --check

# ---------------------------------------------------------------- register
function ConvertTo-WslPath([string]$Path) {
    $p = $Path -replace '\\', '/'
    if ($p -match '^([A-Za-z]):(.*)$') {
        return "/mnt/$($matches[1].ToLower())$($matches[2])"
    }
    return $p
}

if ($Register) {
    $cli = Get-Command $Client -ErrorAction SilentlyContinue
    if (-not $cli) {
        Write-Warning "$Client not found on PATH. Register manually:"
        Write-Host "  Windows: $Client mcp add --scope user cam350 -- `"$serverExe`""
        Write-Host "  WSL:     free-code mcp add --scope user cam350 -- `"$(ConvertTo-WslPath $serverExe)`""
    }
    else {
        # A .cmd/.bat client is usually a WSL shim (e.g. wsl.exe ... free-code %*).
        # Windows backslashes are eaten by WSL argument parsing, so pass the
        # /mnt/c/... form in that case.
        $isShim = $cli.Source -match '\.(cmd|bat)$'
        $regExe = if ($isShim) { ConvertTo-WslPath $serverExe } else { $serverExe }
        Write-Step "Registering with $Client (user scope)"
        & $cli.Source mcp remove cam350 --scope user 2>$null | Out-Null
        & $cli.Source mcp add --scope user cam350 -- $regExe
        if ($LASTEXITCODE -ne 0) { throw "MCP registration failed" }
    }
}

Write-Host ""
Write-Host "Installed. Server command:" -ForegroundColor Green
Write-Host "  $serverExe"
Write-Host ""
Write-Host "Manual registration:"
Write-Host "  Windows: $Client mcp add --scope user cam350 -- `"$serverExe`""
Write-Host "  WSL:     free-code mcp add --scope user cam350 -- `"$(ConvertTo-WslPath $serverExe)`""
Write-Host ""
Write-Host "Set CAM350_EXE if auto-detection fails, e.g.:"
Write-Host '  setx CAM350_EXE "C:\Program Files (x86)\DownStream Technologies\2015\CAM350 12.1\camnt95.exe"'
