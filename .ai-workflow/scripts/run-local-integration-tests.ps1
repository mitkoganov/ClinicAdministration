# AI: Run Local Integration Tests
# The ONLY script permitted to set ALLOW_DESTRUCTIVE_TEST_DB_RESET=true.
# It earns that permission by independently verifying, every time, that the
# target is genuinely the repository's own dedicated, disposable
# `postgres-test` Compose service (see docker-compose.yml) - never by
# trusting an ambient DATABASE_URL or a machine-specific guess. The generic
# quality-gate script (run-tests.ps1) must never do this itself.
#
# Usage:
#   .ai-workflow/scripts/run-local-integration-tests.ps1              # start, test, stop
#   .ai-workflow/scripts/run-local-integration-tests.ps1 -KeepRunning # start, test, leave running
#   .ai-workflow/scripts/run-local-integration-tests.ps1 -- -k slug   # extra pytest args after --

param(
    [switch]$KeepRunning,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

. "$PSScriptRoot\common.ps1"

$root = Get-RepoRoot
Set-Location $root

# --- Constants describing the repository's OWN dedicated test service.
# These must exactly match the `postgres-test` service in docker-compose.yml
# (a tracked file) - keep both in sync if either changes. Nothing here is a
# guess about a developer's machine: it is this repository's own tracked,
# disposable, tmpfs-backed test database, never the `postgres` service used
# for normal development.
$ComposeService = "postgres-test"
$ComposeContainer = "clinic-admin-platform-postgres-test-1"
$TestHost = "127.0.0.1"
$TestPort = 5555
$TestUser = "clinic"
$TestPassword = "clinic"
$TestDbName = "clinic_admin_test"

if (-not (Test-CommandAvailable "docker")) {
    Write-Error "Docker is required to run the dedicated test database. Install Docker Desktop and retry."
    exit 1
}

Write-Host "Starting dedicated test database service '$ComposeService' (profile: test)..."
& docker compose --profile test up -d $ComposeService
if ($LASTEXITCODE -ne 0) {
    Write-Error "`docker compose --profile test up -d $ComposeService` failed (exit $LASTEXITCODE)."
    exit 1
}

Write-Host "Waiting for '$ComposeService' to become healthy..."
$deadline = (Get-Date).AddSeconds(60)
$healthy = $false
while ((Get-Date) -lt $deadline) {
    $status = (& docker inspect -f '{{.State.Health.Status}}' $ComposeContainer 2>$null)
    if ($status -eq "healthy") { $healthy = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $healthy) {
    Write-Error "'$ComposeService' did not become healthy within 60s. Run 'docker compose --profile test logs $ComposeService' to investigate."
    exit 1
}

# --- Independent verification. Refuses to authorize destructive reset
# unless every check below passes - this is what makes it safe for THIS
# script (and only this script) to set ALLOW_DESTRUCTIVE_TEST_DB_RESET.
$actualDb = (& docker exec $ComposeContainer printenv POSTGRES_DB 2>$null)
if ($actualDb -ne $TestDbName) {
    Write-Error "Refusing to authorize destructive reset: container '$ComposeContainer' reports POSTGRES_DB='$actualDb', expected '$TestDbName'. This does not look like the repository's dedicated test service."
    exit 1
}
if ($TestDbName -notmatch '_test$' -and $TestDbName -notmatch 'test') {
    Write-Error "Refusing to authorize destructive reset: configured database name '$TestDbName' does not look test-only."
    exit 1
}

$testDatabaseUrl = "postgresql+psycopg://${TestUser}:${TestPassword}@${TestHost}:${TestPort}/${TestDbName}"

if ($env:DATABASE_URL -and $env:DATABASE_URL -eq $testDatabaseUrl) {
    Write-Error "Refusing to authorize destructive reset: TEST_DATABASE_URL would equal the ambient DATABASE_URL."
    exit 1
}

Write-Host "Verified: '$ComposeContainer' is the repository's dedicated, disposable test service."

$backend = Join-Path $root "backend"
$venvPython = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Error "backend/.venv not found. Run: python -m venv backend/.venv; backend/.venv/Scripts/python -m pip install -e `"backend[dev]`""
    exit 1
}

Write-Host "Running the integration test suite against $TestHost`:$TestPort/$TestDbName..."
$previousTestDatabaseUrl = $env:TEST_DATABASE_URL
$previousAllowDestructive = $env:ALLOW_DESTRUCTIVE_TEST_DB_RESET
try {
    $env:TEST_DATABASE_URL = $testDatabaseUrl
    $env:ALLOW_DESTRUCTIVE_TEST_DB_RESET = "true"
    $pytestFullArgs = @("-m", "pytest", "-m", "integration") + $PytestArgs
    # pytest must run with its rootdir at backend/ (where pyproject.toml
    # lives) - running from $root makes backend/tests/conftest.py look like
    # a non-top-level conftest to pytest and fails collection outright.
    Push-Location $backend
    try {
        & $venvPython @pytestFullArgs
        $pytestExitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
} finally {
    $env:TEST_DATABASE_URL = $previousTestDatabaseUrl
    $env:ALLOW_DESTRUCTIVE_TEST_DB_RESET = $previousAllowDestructive
}

if ($KeepRunning) {
    Write-Host "Leaving '$ComposeService' running (-KeepRunning). Stop it later with: docker compose --profile test stop $ComposeService"
} else {
    Write-Host "Stopping '$ComposeService'..."
    & docker compose --profile test stop $ComposeService | Out-Null
}

exit $pytestExitCode
