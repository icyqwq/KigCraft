param(
    [Parameter(Mandatory = $true)]
    [string]$KeyPath,

    [Parameter(Mandatory = $true)]
    [string]$SshTarget,

    [Parameter(Mandatory = $true)]
    [string]$RemoteAppDir,

    [string]$ComposeFile = "docker-compose.yml"
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$archive = Join-Path $env:TEMP ("kigcraft-deploy-{0}.tar" -f ([Guid]::NewGuid().ToString("N")))
$remoteArchive = "/tmp/kigcraft-deploy.tar"
$remoteScript = "/tmp/kigcraft-deploy.sh"
$scriptPath = $null
$pushed = $false

try {
    Push-Location $root
    $pushed = $true
    try {
        git diff --quiet
        git diff --cached --quiet
    } catch {
        throw "Working tree must be clean before deployment."
    }

    git archive --format=tar --output=$archive HEAD
    Pop-Location
    $pushed = $false

    scp -i $KeyPath $archive "${SshTarget}:$remoteArchive"

    $script = @"
set -euo pipefail
REMOTE_APP_DIR='$RemoteAppDir'
COMPOSE_FILE='$ComposeFile'
mkdir -p "\$REMOTE_APP_DIR"
tar -xf "$remoteArchive" -C "\$REMOTE_APP_DIR"
cd "\$REMOTE_APP_DIR"

if [ ! -f .env ]; then
  echo "Missing production .env in \$REMOTE_APP_DIR" >&2
  exit 1
fi

if grep -Eq '^GENERATION_PROVIDER=(fixture|mock)$' .env; then
  echo "Refusing to deploy fixture/mock generation provider." >&2
  exit 1
fi

if grep -Eq '^ALLOW_FIXTURE_GENERATION=true$' .env; then
  echo "Refusing to deploy with ALLOW_FIXTURE_GENERATION=true." >&2
  exit 1
fi

docker compose -f "\$COMPOSE_FILE" up -d --build api worker frontend
docker compose -f "\$COMPOSE_FILE" ps
"@

    $scriptPath = Join-Path $env:TEMP ("kigcraft-deploy-{0}.sh" -f ([Guid]::NewGuid().ToString("N")))
    Set-Content -LiteralPath $scriptPath -Value $script -Encoding UTF8
    scp -i $KeyPath $scriptPath "${SshTarget}:$remoteScript"
    ssh -i $KeyPath $SshTarget "bash $remoteScript"
} finally {
    if (Test-Path -LiteralPath $archive) {
        Remove-Item -LiteralPath $archive -Force
    }
    if ($scriptPath -and (Test-Path -LiteralPath $scriptPath)) {
        Remove-Item -LiteralPath $scriptPath -Force
    }
    if ($pushed) {
        Pop-Location
    }
}
