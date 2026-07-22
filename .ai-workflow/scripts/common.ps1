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

      Pass -StandardInputPath (a file path) instead of embedding large or
      multiline content (e.g. an LLM prompt) as an -ArgumentList element.
      Multiline strings passed through -ArgumentList are not safe: depending
      on how the target executable resolves (directly vs. via a cmd.exe/.cmd
      shim, as most npm-installed CLIs do on Windows), the string can be
      re-split on whitespace/newlines before the target process ever sees
      it, silently truncating or corrupting the input. Reading from a file
      via stdin has no such ambiguity.
    #>
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [string]$WorkingDirectory = (Get-RepoRoot),
        [string]$StandardInputPath
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
            # No manual quoting here - ProcessStartInfo.ArgumentList quotes
            # each element (including paths containing spaces) itself.
            $actualFile = "$env:SystemRoot\System32\cmd.exe"
            $actualArgs = @("/c", $resolved.Source) + $ArgumentList
        } elseif ($resolved.Source -match '\.ps1$' -and (Test-Path $cmdSibling)) {
            $actualFile = "$env:SystemRoot\System32\cmd.exe"
            $actualArgs = @("/c", $cmdSibling) + $ArgumentList
        } elseif ($resolved.Source -match '\.ps1$') {
            $actualFile = (Get-Process -Id $PID).Path
            $actualArgs = @("-NoProfile", "-File", $resolved.Source) + $ArgumentList
        } else {
            $actualFile = $resolved.Source
        }
    }

    if ($StandardInputPath -and -not (Test-Path $StandardInputPath)) {
        throw "StandardInputPath does not exist: $StandardInputPath"
    }

    # Uses System.Diagnostics.ProcessStartInfo.ArgumentList directly, NOT
    # Start-Process -ArgumentList: Start-Process joins a string[] into one
    # command-line string using its own quoting, and on this host that
    # quoting is unreliable for elements that merely contain a space (e.g.
    # a single argv token like "not integration") - confirmed by a real
    # failure where pytest received "not" and "integration" as two separate
    # arguments instead of one. ProcessStartInfo.ArgumentList quotes each
    # element as a discrete argv token per Win32 CreateProcess conventions,
    # with no manual escaping required here.
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $actualFile
    foreach ($a in $actualArgs) { $psi.ArgumentList.Add($a) }
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $psi.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
    if ($StandardInputPath) {
        $psi.RedirectStandardInput = $true
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $psi
    $process.Start() | Out-Null

    if ($StandardInputPath) {
        $inputBytes = [System.IO.File]::ReadAllBytes($StandardInputPath)
        $process.StandardInput.BaseStream.Write($inputBytes, 0, $inputBytes.Length)
        $process.StandardInput.BaseStream.Flush()
        $process.StandardInput.Close()
    }

    # Read both streams before WaitForExit to avoid deadlocking on a full
    # OS pipe buffer if the child writes a lot to stdout and stderr.
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()

    # Command is metadata for the report only - it never contains the
    # actual stdin payload (see StandardInputPath above), so it is safe to
    # join with spaces even though it is not literally re-runnable.
    $commandDisplay = "$FilePath $($ArgumentList -join ' ')".Trim()
    if ($StandardInputPath) {
        $commandDisplay = "$commandDisplay  (stdin piped from: $StandardInputPath)"
    }

    [PSCustomObject]@{
        Command    = $commandDisplay
        ExitCode   = $process.ExitCode
        Stdout     = $stdout
        Stderr     = $stderr
        TimestampsAreCaller = $true
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
    $parentDir = Split-Path -Parent $path
    if ($parentDir -and -not (Test-Path $parentDir)) {
        New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
    }
    $Content | Out-File -FilePath $path -Encoding utf8NoBOM
    Write-Host "Report written: $path"
    return $path
}

$script:ExcludedDirNames = @(
    ".venv", "node_modules", ".next", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".git", ".codex", "dist", "build",
    "coverage", "egg-info"
)
$script:ExcludedFileNames = @(".env")
$script:ExcludedExtensions = @(
    ".pyc", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
    ".zip", ".gz", ".tar", ".exe", ".dll", ".so", ".whl", ".db",
    ".sqlite", ".sqlite3"
)
$script:SecretNamePatterns = @(
    "*.pem", "*.key", "id_rsa*", "id_ed25519*", "*.pfx", "*.p12",
    "credentials*.json", "secrets*", "*.env.local", "*.env.*.local"
)

function Test-ExcludedRelativePath {
    <#
      True if a repo-relative path must never be read into a review packet,
      an audit log, or any other generated report: secrets, credentials,
      virtual environments, dependency trees, build output, and the .codex
      directory. This is a denylist checked by directory-name segment,
      exact filename, extension, and common secret-file glob patterns - not
      by content, so callers should still skip unreadable/binary files
      independently (see Test-BinaryFile).
    #>
    param([Parameter(Mandatory)][string]$RelativePath)

    $normalized = $RelativePath -replace '\\', '/'
    $segments = $normalized -split '/'

    foreach ($segment in $segments) {
        if ($script:ExcludedDirNames -contains $segment) { return $true }
    }

    $name = Split-Path -Leaf $normalized
    if ($script:ExcludedFileNames -contains $name) { return $true }
    if ($name -ieq ".env") { return $true }

    $ext = [System.IO.Path]::GetExtension($name)
    if ($ext -and ($script:ExcludedExtensions -contains $ext.ToLowerInvariant())) { return $true }

    foreach ($pattern in $script:SecretNamePatterns) {
        if ($name -like $pattern) { return $true }
    }

    return $false
}

function Test-PathInsideRepoRoot {
    <#
      Defense-in-depth boundary check: resolves RelativePath against Root
      and confirms the result is still inside Root. Repo-relative paths from
      `git status`/`git diff` are never expected to escape the repo, but
      nothing that reads a path off of external tool output should trust
      that invariant silently.
    #>
    param(
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$RelativePath
    )
    $fullRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    $fullPath = [System.IO.Path]::GetFullPath((Join-Path $Root $RelativePath))
    return $fullPath.StartsWith($fullRoot, [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-BinaryFile {
    <#
      Heuristic binary detector: reads up to the first 8000 bytes and
      returns true if a NUL byte is found, or if the file cannot be read at
      all (treated as unsafe to embed as text either way).

      Resolves a relative Path against PowerShell's current location
      explicitly - [System.IO.File] APIs resolve relative paths against
      .NET's Environment.CurrentDirectory, which is not guaranteed to track
      PowerShell's Set-Location/$PWD, so a bare relative path here could
      silently resolve against the wrong directory and be misreported as
      unreadable (hence "binary").
    #>
    param([Parameter(Mandatory)][string]$Path)
    if (-not [System.IO.Path]::IsPathRooted($Path)) {
        $Path = Join-Path (Get-Location).Path $Path
    }
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        try {
            $bufferSize = [Math]::Min(8000, [int]$stream.Length)
            if ($bufferSize -eq 0) { return $false }
            $buffer = New-Object byte[] $bufferSize
            $read = $stream.Read($buffer, 0, $bufferSize)
            for ($i = 0; $i -lt $read; $i++) {
                if ($buffer[$i] -eq 0) { return $true }
            }
            return $false
        } finally {
            $stream.Dispose()
        }
    } catch {
        return $true
    }
}

function Write-Utf8File {
    <#
      Writes text content as UTF-8 without a BOM, consistently, regardless
      of PowerShell host/version default-encoding quirks. Use this (not
      Out-File/Set-Content with an implicit encoding) for any file whose
      content will later be read back and re-embedded elsewhere (e.g. a
      prompt file passed to another process) or that must round-trip
      non-ASCII characters (em dashes, etc.) without corruption.
    #>
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Content
    )
    $parentDir = Split-Path -Parent $Path
    if ($parentDir -and -not (Test-Path $parentDir)) {
        New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
    }
    [System.IO.File]::WriteAllText($Path, $Content, [System.Text.UTF8Encoding]::new($false))
}

function Test-GitDiffCheck {
    <#
      Runs `git diff --check` (whitespace-error check) against a
      disposable, temporary index file - never the repository's real index
      (.git/index) - so this never disturbs anything the caller has
      already staged with `git add` for their own purposes. Pass
      -ExcludePathspecs to keep generated/non-source paths (e.g. reports/,
      which holds this very tooling's own verbatim-captured transcripts)
      out of the check entirely, rather than accepting whitespace errors in
      real source files.
    #>
    param(
        [Parameter(Mandatory)][string]$Root,
        [string[]]$ExcludePathspecs = @()
    )

    $realIndex = Join-Path $Root ".git\index"
    $tempIndex = Join-Path ([System.IO.Path]::GetTempPath()) "quality-gate-index-$([guid]::NewGuid().ToString('N')).tmp"
    if (Test-Path $realIndex) {
        Copy-Item $realIndex $tempIndex
    }

    $previousIndexFile = $env:GIT_INDEX_FILE
    try {
        $env:GIT_INDEX_FILE = $tempIndex
        $addArgs = @("-C", $Root, "add", "-A", "--", ".") + $ExcludePathspecs
        & git @addArgs *>$null
        $output = (& git -C $Root diff --check --cached 2>&1 | Out-String).Trim()
        $exitCode = $LASTEXITCODE
        [PSCustomObject]@{ ExitCode = $exitCode; Output = $output }
    } finally {
        if ($previousIndexFile) {
            $env:GIT_INDEX_FILE = $previousIndexFile
        } else {
            Remove-Item Env:\GIT_INDEX_FILE -ErrorAction SilentlyContinue
        }
        Remove-Item $tempIndex -ErrorAction SilentlyContinue
    }
}
