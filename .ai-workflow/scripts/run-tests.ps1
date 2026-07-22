# AI: Run Quality Gates
# Runs backend (ruff, mypy, unit pytest, integration pytest) and frontend
# (lint, typecheck, build) checks. Captures every command's real exit code -
# never reports success without running it.
#
# This script never authorizes destructive test-database setup itself: it
# either uses TEST_DATABASE_URL/ALLOW_DESTRUCTIVE_TEST_DB_RESET the caller
# (a developer or CI) already set explicitly, or delegates to
# run-local-integration-tests.ps1, which independently verifies the target
# is the repository's own dedicated, disposable postgres-test service
# before setting them. A missing/unusable integration-test configuration is
# reported as a FAILED check here, never silently skipped.

. "$PSScriptRoot\common.ps1"

$root = Get-RepoRoot
$results = @()

function Add-Result($name, $exitCode, $output) {
    $script:results += [PSCustomObject]@{ Name = $name; ExitCode = $exitCode; Output = $output }
}

$backend = Join-Path $root "backend"
$venvPython = Join-Path $backend ".venv\Scripts\python.exe"

if (Test-Path $venvPython) {
    foreach ($check in @(
        @{ Name = "backend-ruff"; Args = @("-m", "ruff", "check", ".") },
        @{ Name = "backend-ruff-format"; Args = @("-m", "ruff", "format", "--check", ".") },
        @{ Name = "backend-mypy"; Args = @("-m", "mypy", "app") },
        @{ Name = "backend-unit-pytest"; Args = @("-m", "pytest", "-m", "not integration") }
    )) {
        $r = Invoke-CapturedCommand -FilePath $venvPython -ArgumentList $check.Args -WorkingDirectory $backend
        Add-Result $check.Name $r.ExitCode ($r.Stdout + $r.Stderr)
    }

    if ($env:TEST_DATABASE_URL -and $env:ALLOW_DESTRUCTIVE_TEST_DB_RESET -eq "true") {
        # Caller (developer shell or CI) already explicitly authorized a
        # specific target - use it as given, no derivation, no guessing.
        $r = Invoke-CapturedCommand -FilePath $venvPython `
            -ArgumentList @("-m", "pytest", "-m", "integration") -WorkingDirectory $backend
        Add-Result "backend-integration-pytest" $r.ExitCode ($r.Stdout + $r.Stderr)
    } else {
        # No explicit authorization present - delegate to the wrapper, which
        # starts/verifies the repository's own dedicated test service and
        # only then sets the destructive opt-in for its own child process.
        $pwshExe = (Get-Process -Id $PID).Path
        $wrapperPath = Join-Path $root ".ai-workflow\scripts\run-local-integration-tests.ps1"
        $r = Invoke-CapturedCommand -FilePath $pwshExe `
            -ArgumentList @("-NoProfile", "-File", $wrapperPath) -WorkingDirectory $root
        Add-Result "backend-integration-pytest" $r.ExitCode ($r.Stdout + $r.Stderr)
    }
} else {
    Add-Result "backend-venv" 1 "backend/.venv not found - run: python -m venv backend/.venv; backend/.venv/Scripts/python -m pip install -e `"backend[dev]`""
}

$frontend = Join-Path $root "frontend"
if (Test-Path (Join-Path $frontend "node_modules")) {
    foreach ($check in @(
        @{ Name = "frontend-lint"; Args = @("run", "lint") },
        @{ Name = "frontend-typecheck"; Args = @("run", "typecheck") },
        @{ Name = "frontend-build"; Args = @("run", "build") }
    )) {
        $r = Invoke-CapturedCommand -FilePath "npm" -ArgumentList $check.Args -WorkingDirectory $frontend
        Add-Result $check.Name $r.ExitCode ($r.Stdout + $r.Stderr)
    }
} else {
    Add-Result "frontend-install" 1 "frontend/node_modules not found - run: npm install (inside frontend/)"
}

# Docker checks - required by tasks/current/task.md's quality-gate list
# ("Docker Compose config validation", "backend image build"). These never
# authorize destructive database access themselves; they only validate
# config and build an image.
if (Test-CommandAvailable "docker") {
    $r = Invoke-CapturedCommand -FilePath "docker" -ArgumentList @("compose", "config") -WorkingDirectory $root
    Add-Result "docker-compose-config" $r.ExitCode ($r.Stdout + $r.Stderr)

    $r = Invoke-CapturedCommand -FilePath "docker" `
        -ArgumentList @("compose", "--profile", "test", "config") -WorkingDirectory $root
    Add-Result "docker-compose-test-profile-config" $r.ExitCode ($r.Stdout + $r.Stderr)

    $r = Invoke-CapturedCommand -FilePath "docker" -ArgumentList @("compose", "build", "backend") -WorkingDirectory $root
    Add-Result "backend-docker-image-build" $r.ExitCode ($r.Stdout + $r.Stderr)
} else {
    $dockerMissingMessage = "docker not found on PATH - required by tasks/current/task.md's quality-gate list " +
        "(`"Docker Compose config validation`" / `"backend image build`")."
    Add-Result "docker-compose-config" 1 $dockerMissingMessage
    Add-Result "docker-compose-test-profile-config" 1 $dockerMissingMessage
    Add-Result "backend-docker-image-build" 1 $dockerMissingMessage
}

# git diff --check - required by tasks/current/task.md's quality-gate list.
# Uses a disposable temporary index (see Test-GitDiffCheck in common.ps1),
# never the repository's real staging area, and excludes reports/ (this
# tooling's own generated, verbatim-captured transcripts - not source) so a
# quoted "Generating static pages... " progress line from a captured `npm
# run build` transcript can never fail this check.
$gitDiffCheck = Test-GitDiffCheck -Root $root -ExcludePathspecs @(":(exclude)reports")
Add-Result "git-diff-check" $gitDiffCheck.ExitCode $gitDiffCheck.Output

$fence = [string]::new('`', 3)
$lines = @("# Quality gate run", "")
foreach ($r in $results) {
    $status = if ($r.ExitCode -eq 0) { "PASS" } else { "FAIL" }
    $lines += "## $($r.Name) - $status (exit $($r.ExitCode))"
    $lines += ""
    $lines += $fence
    $lines += $r.Output
    $lines += $fence
    $lines += ""
}
$report = $lines -join "`n"
Write-WorkflowReport -Name "quality-gates-latest.md" -Content $report

$failed = $results | Where-Object { $_.ExitCode -ne 0 }
if ($failed) {
    Write-Error "$($failed.Count) check(s) failed: $($failed.Name -join ', ')"
    exit 1
}
Write-Host "All quality gates passed."
