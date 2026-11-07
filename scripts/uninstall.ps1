#requires -Version 5.1
<#
.SYNOPSIS
    Remove the CAM350 MCP server and (optionally) its runtime data.

.PARAMETER InstallDir
    Location of the virtual environment.  Default: %LOCALAPPDATA%\Programs\cam350-mcp.

.PARAMETER RemoveData
    Also delete %LOCALAPPDATA%\cam350-mcp (runtime files, logs, screenshots).
    Exported files that you wrote elsewhere are never touched.

.PARAMETER Client
    MCP client to unregister from: claude (default) or free-code.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\cam350-mcp"),
    [switch]$RemoveData,
    [ValidateSet("claude", "free-code")]
    [string]$Client = "claude"
)

$ErrorActionPreference = "Stop"

$cli = Get-Command $Client -ErrorAction SilentlyContinue
if ($cli) {
    Write-Host "Removing MCP registration 'cam350'..."
    & $cli.Source mcp remove cam350 --scope user 2>$null | Out-Null
}
else {
    Write-Warning "$Client not found; remove the 'cam350' entry from your MCP config manually."
}

if (Test-Path $InstallDir) {
    Write-Host "Removing $InstallDir..."
    Remove-Item -Recurse -Force $InstallDir
}

if ($RemoveData) {
    $data = Join-Path $env:LOCALAPPDATA "cam350-mcp"
    if (Test-Path $data) {
        Write-Host "Removing $data..."
        Remove-Item -Recurse -Force $data
    }
}
else {
    Write-Host "Runtime data kept at $(Join-Path $env:LOCALAPPDATA 'cam350-mcp') (use -RemoveData to delete)."
}

Write-Host "Done." -ForegroundColor Green
