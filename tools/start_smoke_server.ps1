$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$python = $env:PYTHON
if (-not $python) {
  $command = Get-Command python -ErrorAction SilentlyContinue
  if ($command) {
    $python = $command.Source
  } else {
    $command = Get-Command py -ErrorAction SilentlyContinue
    if ($command) {
      $python = $command.Source
    }
  }
}
if (-not $python) {
  throw "Python was not found. Set PYTHON or add python to PATH."
}
$server = Join-Path $root "tools\local_smoke_server.py"
$runtime = Join-Path $root ".smoke-runtime"
$stdout = Join-Path $runtime "server.out.log"
$stderr = Join-Path $runtime "server.err.log"

New-Item -ItemType Directory -Force -Path $runtime | Out-Null

$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $python
$startInfo.Arguments = "`"$server`""
$startInfo.WorkingDirectory = $root
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true

$process = [System.Diagnostics.Process]::new()
$process.StartInfo = $startInfo
if (!$process.Start()) {
  throw "Failed to start smoke server"
}

Write-Output $process.Id
