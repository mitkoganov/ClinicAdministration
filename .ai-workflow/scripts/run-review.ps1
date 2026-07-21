# AI: Review Current Changes
# Runs Codex CLI as an independent, read-only reviewer over the current diff.
# codex exec --sandbox read-only guarantees no write access to the repository.

. "$PSScriptRoot\common.ps1"

$root = Get-RepoRoot
Set-Location $root

if (-not (Test-CommandAvailable "codex")) {
    Write-Error "codex CLI not found on PATH. Install with: npm install -g @openai/codex"
    exit 1
}

$diffStat = & git diff --stat
if ([string]::IsNullOrWhiteSpace($diffStat)) {
    Write-Host "No uncommitted changes to review (git diff --stat is empty)."
    exit 0
}

$prompt = Get-Content -Raw (Join-Path $root ".ai-workflow\prompts\review.md")

# Inspect `codex exec --help` before adding new flags here; only supported
# flags for the locally installed version (validated: codex-cli 0.144.6) are used.
$result = Invoke-CapturedCommand -FilePath "codex" -ArgumentList @(
    "exec", "--sandbox", "read-only", "--skip-git-repo-check", "-C", $root, $prompt
)

$fence = [string]::new('`', 3)
$lines = @(
    "# Codex review - read-only",
    "Command: $($result.Command)",
    "Exit code: $($result.ExitCode)",
    "",
    "## Diff stat reviewed",
    $fence,
    $diffStat,
    $fence,
    "",
    "## Codex output",
    $result.Stdout,
    "",
    "## Stderr",
    $result.Stderr
)
Write-WorkflowReport -Name "review-latest.md" -Content ($lines -join "`n")

Write-Host "Review complete. Findings written to reports/review-latest.md - stopping for human approval."
