# AI: Run Current Task
# Invokes the implementation agent (Claude Code) against tasks/current/task.md
# on a feature branch. Never runs on main/master. Never commits.

. "$PSScriptRoot\common.ps1"

$root = Get-RepoRoot
Set-Location $root

$branch = & git rev-parse --abbrev-ref HEAD
$branch = $branch.Trim()
$config = Get-WorkflowConfig

if ($config.protectedBranches -contains $branch) {
    Write-Error "Current branch '$branch' is protected. Create a feature branch first, e.g.:`n  git checkout -b feature/med-001-validate-foundation"
    exit 1
}

$taskPath = Join-Path $root "tasks\current\task.md"
if (-not (Test-Path $taskPath)) {
    Write-Error "No active task found at tasks/current/task.md"
    exit 1
}

if (-not (Test-CommandAvailable "claude")) {
    Write-Host "Claude Code CLI is not on PATH in this environment (only the VS Code extension was detected at setup time)."
    Write-Host "This step is a no-op here by design - implement the task in tasks/current/task.md using the Claude Code VS Code extension directly, following AGENTS.md and CLAUDE.md."
    Write-Host "No result is fabricated. Nothing was run."
    exit 2
}

# If/when the claude CLI is available, inspect its help before adding flags here
# rather than assuming flags from another version.
$prompt = Get-Content -Raw (Join-Path $root ".ai-workflow\prompts\implement.md")
$taskContent = Get-Content -Raw $taskPath

$result = Invoke-CapturedCommand -FilePath "claude" -ArgumentList @("--print", "$prompt`n`n$taskContent")
$lines = @(
    "# Implement run - branch: $branch",
    "Command: $($result.Command)",
    "Exit code: $($result.ExitCode)",
    "",
    "## Stdout",
    $result.Stdout,
    "",
    "## Stderr",
    $result.Stderr
)
Write-WorkflowReport -Name "implement-latest.md" -Content ($lines -join "`n")
