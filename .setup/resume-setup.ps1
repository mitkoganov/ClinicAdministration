# Resumable setup entrypoint.
# Reads .setup/setup-state.json and only continues incomplete phases.
# Safe to re-run: never repeats a completed phase, never fabricates success.

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $repoRoot ".setup\setup-state.json"

if (-not (Test-Path $statePath)) {
    throw "No setup-state.json found at $statePath. This script only resumes an existing setup."
}

$state = Get-Content -Raw -Path $statePath | ConvertFrom-Json

Write-Host "Completed phases: $($state.completedPhases -join ', ')"
Write-Host "Pending phases:   $($state.pendingPhases -join ', ')"

if ($state.pendingPhases -contains "docker-desktop-manual-install") {
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $dockerCmd) {
        Write-Host ""
        Write-Host "Docker Desktop is still not installed. This requires an interactive UAC approval that cannot be automated:"
        Write-Host "  winget install --id Docker.DockerDesktop --exact"
        Write-Host "Then launch Docker Desktop, accept its terms, and re-run this script."
        exit 2
    }

    $engineUp = $false
    try {
        docker info --format '{{.ServerVersion}}' | Out-Null
        $engineUp = ($LASTEXITCODE -eq 0)
    } catch { $engineUp = $false }

    if (-not $engineUp) {
        Write-Host "Docker CLI is present but the engine is not running. Start Docker Desktop and re-run this script."
        exit 2
    }

    Write-Host "Docker engine detected as running. Validating docker-compose.yml ..."
    Push-Location $repoRoot
    try {
        docker compose config | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "docker compose config failed" }
        Write-Host "docker compose config: OK"

        $state.pendingPhases = @($state.pendingPhases | Where-Object { $_ -ne "docker-desktop-manual-install" })
        $state.completedPhases += "docker-desktop-installed-and-validated"
        $state.validationResults.dockerEngine = "running"
    } finally {
        Pop-Location
    }
}

$state | ConvertTo-Json -Depth 10 | Out-File -FilePath $statePath -Encoding utf8

Write-Host ""
Write-Host "State updated: $statePath"
Write-Host "Remaining pending phases: $($state.pendingPhases -join ', ')"
if ($state.pendingPhases.Count -eq 0) {
    Write-Host "All phases complete. Run the 'AI: Run Quality Gates' VS Code task, or:"
    Write-Host "  pwsh -NoProfile -File .ai-workflow/scripts/run-tests.ps1"
}
