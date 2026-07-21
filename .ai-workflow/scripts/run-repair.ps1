# AI: Repair Review Findings
# Re-invokes the implementation agent against reports/review-latest.md.
# Enforces maxRepairAttempts (default 2) via .ai-workflow/state/repair-count.txt.

. "$PSScriptRoot\common.ps1"

$root = Get-RepoRoot
Set-Location $root

$config = Get-WorkflowConfig
$maxAttempts = $config.maxRepairAttempts

$stateDir = Join-Path $root ".ai-workflow\state"
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
$counterPath = Join-Path $stateDir "repair-count.txt"

$attempt = 1
if (Test-Path $counterPath) {
    $attempt = [int](Get-Content $counterPath) + 1
}

if ($attempt -gt $maxAttempts) {
    Write-Error "Maximum repair attempts ($maxAttempts) already reached. Stopping - hand back to a human reviewer. Delete $counterPath to reset after human review."
    exit 1
}

$branch = (& git rev-parse --abbrev-ref HEAD).Trim()
if ($config.protectedBranches -contains $branch) {
    Write-Error "Current branch '$branch' is protected. Repair must run on a feature branch."
    exit 1
}

$reviewPath = Join-Path $root "reports\review-latest.md"
if (-not (Test-Path $reviewPath)) {
    Write-Error "No reports/review-latest.md found. Run 'AI: Review Current Changes' first."
    exit 1
}

if (-not (Test-CommandAvailable "claude")) {
    Write-Host "Claude Code CLI is not on PATH in this environment. Address the findings in reports/review-latest.md manually via the Claude Code VS Code extension, following .ai-workflow/prompts/repair.md."
    exit 2
}

$promptTemplate = Get-Content -Raw (Join-Path $root ".ai-workflow\prompts\repair.md")
$prompt = $promptTemplate.Replace("{ATTEMPT_NUMBER}", "$attempt").Replace("{MAX_ATTEMPTS}", "$maxAttempts")
$reviewContent = Get-Content -Raw $reviewPath

$result = Invoke-CapturedCommand -FilePath "claude" -ArgumentList @("--print", "$prompt`n`n$reviewContent")

Set-Content -Path $counterPath -Value $attempt

$lines = @(
    "# Repair attempt $attempt of $maxAttempts - branch: $branch",
    "Command: $($result.Command)",
    "Exit code: $($result.ExitCode)",
    "",
    "## Stdout",
    $result.Stdout,
    "",
    "## Stderr",
    $result.Stderr
)
Write-WorkflowReport -Name "repair-latest.md" -Content ($lines -join "`n")
