param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 18100,
    [string]$Root = ""
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = if ($Root) {
    (Resolve-Path $Root).Path
} else {
    (Resolve-Path (Join-Path $scriptDir "..")).Path
}

if (-not $env:KIG_PREVIEW_ROOT) {
    $env:KIG_PREVIEW_ROOT = $repoRoot
}
if (-not $env:CODEX_BRIDGE_HOST) {
    $env:CODEX_BRIDGE_HOST = $HostName
}
if (-not $env:CODEX_BRIDGE_PORT) {
    $env:CODEX_BRIDGE_PORT = [string]$Port
}
if (-not $env:CODEX_BRIDGE_TOKEN) {
    $env:CODEX_BRIDGE_TOKEN = "change-me-local-bridge-token"
}
if (-not $env:CODEX_PATH) {
    $codexCandidates = @(
        (Join-Path $repoRoot ".tools\codex.exe"),
        (Join-Path (Split-Path -Parent (Split-Path -Parent $repoRoot)) ".tools\codex.exe"),
        "codex"
    )
    foreach ($candidate in $codexCandidates) {
        if ($candidate -eq "codex" -or (Test-Path $candidate)) {
            $env:CODEX_PATH = $candidate
            break
        }
    }
}

$python = Join-Path $repoRoot "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

Write-Host "Starting Codex bridge at http://$($env:CODEX_BRIDGE_HOST):$($env:CODEX_BRIDGE_PORT)"
Write-Host "Repo root: $($env:KIG_PREVIEW_ROOT)"
Write-Host "Codex path: $($env:CODEX_PATH)"

& $python (Join-Path $repoRoot "tools\codex_bridge.py")
