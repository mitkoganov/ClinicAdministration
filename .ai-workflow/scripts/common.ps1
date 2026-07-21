# Shared helpers for .ai-workflow scripts.
# Safety invariants enforced here:
#   - all commands run with the repo root as working directory, nothing outside it
#   - no command ever reads .env
#   - no command runs on a protected branch during implementation
#   - every invocation's stdout/stderr/exit code is captured verbatim, never fabricated

$ErrorActionPreference = "Stop"

function Get-RepoRoot {
    $root = (& git rev-parse --show-toplevel 2>$null)
    if ([string]::IsNullOrWhiteSpace($root)) {
        throw "Not inside a git repository. Run this script from within clinic-admin-platform."
    }
    return ($root -replace "/", "\")
}

function Get-WorkflowConfig {
    $root = Get-RepoRoot
    $configPath = Join-Path $root ".ai-workflow\config.json"
    return Get-Content -Raw -Path $configPath | ConvertFrom-Json
}

function Assert-NotProtectedBranch {
    param([string]$Action = "this operation")

    $config = Get-WorkflowConfig
    $branch = (& git rev-parse --abbrev-ref HEAD).Trim()

    if ($config.protectedBranches -contains $branch) {
        throw "Refusing to run $Action on protected branch '$branch'. Create or switch to a feature branch first."
    }
    return $branch
}

function Test-CommandAvailable {
    param([Parameter(Mandatory)][string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Assert-NoEnvAccess {
    param([string]$Command)
    if ($Command -match "(?<![\w.])\.env(?!\.example)(?![\w.])") {
        throw "Refusing to run a command that references .env directly: $Command"
    }
}

function Invoke-CapturedCommand {
    <#
      Runs a native command, capturing stdout, stderr, and exit code exactly
      as produced - never summarized or fabricated. Returns a PSCustomObject.
    #>
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [string]$WorkingDirectory = (Get-RepoRoot)
    )

    Assert-NoEnvAccess -Command "$FilePath $($ArgumentList -join ' ')"

    # Windows cannot CreateProcess a .cmd/.bat/.ps1 shim directly (npm, npx,
    # etc. resolve to one of these) - resolve and route through the right host.
    $resolved = Get-Command $FilePath -ErrorAction SilentlyContinue
    $actualFile = $FilePath
    $actualArgs = $ArgumentList
    if ($resolved -and $resolved.Source) {
        $cmdSibling = [System.IO.Path]::ChangeExtension($resolved.Source, "cmd")
        if ($resolved.Source -match '\.(cmd|bat)$') {
            $actualFile = "$env:SystemRoot\System32\cmd.exe"
            $actualArgs = @("/c", "`"$($resolved.Source)`"") + $ArgumentList
        } elseif ($resolved.Source -match '\.ps1$' -and (Test-Path $cmdSibling)) {
            $actualFile = "$env:SystemRoot\System32\cmd.exe"
            $actualArgs = @("/c", "`"$cmdSibling`"") + $ArgumentList
        } elseif ($resolved.Source -match '\.ps1$') {
            $actualFile = (Get-Process -Id $PID).Path
            $actualArgs = @("-NoProfile", "-File", $resolved.Source) + $ArgumentList
        } else {
            $actualFile = $resolved.Source
        }
    }

    $stdoutFile = New-TemporaryFile
    $stderrFile = New-TemporaryFile
    try {
        $process = Start-Process -FilePath $actualFile -ArgumentList $actualArgs `
            -WorkingDirectory $WorkingDirectory -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile

        [PSCustomObject]@{
            Command    = "$FilePath $($ArgumentList -join ' ')"
            ExitCode   = $process.ExitCode
            Stdout     = Get-Content -Raw -Path $stdoutFile -ErrorAction SilentlyContinue
            Stderr     = Get-Content -Raw -Path $stderrFile -ErrorAction SilentlyContinue
            TimestampsAreCaller = $true
        }
    } finally {
        Remove-Item $stdoutFile, $stderrFile -ErrorAction SilentlyContinue
    }
}

function Write-WorkflowReport {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][object]$Content
    )
    $root = Get-RepoRoot
    $reportsDir = Join-Path $root "reports"
    if (-not (Test-Path $reportsDir)) {
        New-Item -ItemType Directory -Path $reportsDir -Force | Out-Null
    }
    $path = Join-Path $reportsDir $Name
    $Content | Out-File -FilePath $path -Encoding utf8
    Write-Host "Report written: $path"
    return $path
}
