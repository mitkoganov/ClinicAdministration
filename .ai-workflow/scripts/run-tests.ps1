# AI: Run Quality Gates
# Runs backend (ruff, mypy, pytest) and frontend (lint, typecheck, build) checks.
# Captures every command's real exit code - never reports success without running it.

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
        @{ Name = "backend-mypy"; Args = @("-m", "mypy", "app") },
        @{ Name = "backend-pytest"; Args = @("-m", "pytest") }
    )) {
        $r = Invoke-CapturedCommand -FilePath $venvPython -ArgumentList $check.Args -WorkingDirectory $backend
        Add-Result $check.Name $r.ExitCode ($r.Stdout + $r.Stderr)
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
