# AI: Review Current Changes
# Runs Codex CLI as an independent reviewer over a self-contained review
# packet - not by letting Codex run its own shell/filesystem tool calls.
#
# Why: Codex's own read-only-sandbox shell tool fails outright on this
# machine (`windows sandbox: CreateProcessWithLogonW failed: 267`), so any
# review that depends on Codex calling `git diff`, `Get-Content`, etc. from
# inside its sandbox produces no review at all (exit 0, but the model
# explicitly reports it couldn't inspect anything). Instead, this script
# collects every input Codex needs - governing docs, task, git status/diff,
# untracked file contents, quality-gate output - into one packet, and
# instructs Codex not to attempt any tool call at all.
#
# The prompt+packet is piped to Codex via stdin (a file, redirected), never
# as a command-line argument - a multiline string handed to -ArgumentList
# can be re-split on whitespace/newlines before the target process sees it.
# See Invoke-CapturedCommand's -StandardInputPath in common.ps1.
#
# Diff baseline: the tracked-file diff/status/name-list always compares
# against an explicit commit - `HEAD` while there is uncommitted work, or
# the merge-base commit on a clean tree - never the bare working-tree-vs-
# index comparison a plain `git diff`/`git diff --name-only` performs. The
# bare form only sees UNSTAGED changes, so a partially staged tree (stage
# file A, leave file B unstaged) silently dropped file A from the packet.
# `git diff HEAD`/`git diff --name-status HEAD` inherently reflect the full
# working-tree state (index + worktree combined) relative to one baseline,
# so a file with both staged and unstaged edits is represented exactly
# once, with its final on-disk content and one combined diff - never two
# separately-collected layers that could double-count or disagree.
#
# -DryRun builds and saves the prompt + packet files (for inspection) and
# stops before invoking Codex at all - use this to check what would be sent
# without spending a Codex call.
#
# -ValidateProvenance builds the packet (like -DryRun) and then asserts the
# embedded-evidence/omitted-files provenance rules hold - no report file is
# ever both embedded as evidence and listed as omitted, generic reports/
# noise and this tooling's own self-referential output stay excluded, and
# the two allowlisted evidence files are embedded whenever present. Exits
# non-zero without invoking Codex if any assertion fails.
#
# -ValidateDiffCollection builds several packets against a disposable,
# throwaway Git repository under the OS temp directory (never this
# repository) covering staged-only/unstaged-only/mixed/added/deleted/
# renamed/untracked/report-noise/clean-tree scenarios, and asserts the
# packet-construction logic (the same Build-ReviewPacket function a real
# review uses - not a re-implemented approximation) handles every one of
# them correctly with no duplicate entries and no mutation of the
# disposable repo's own Git index. Exits non-zero without invoking Codex
# if any assertion fails.

param([switch]$DryRun, [switch]$ValidateProvenance, [switch]$ValidateDiffCollection)

. "$PSScriptRoot\common.ps1"

$root = Get-RepoRoot
$fence = [string]::new('`', 3)

# Fixed, explicit allowlist - never a pattern/wildcard - of the only
# reports/ files ever permitted to be embedded as evidence despite the
# general reports/ exclusion. Adding a new evidence file requires editing
# this list AND adding its own Add-EvidenceSection call site, so arbitrary
# files under reports/ can never bypass exclusion implicitly.
$EvidenceAllowlist = @("reports/quality-gates-latest.md", "reports/migration-validation-latest.md")

$governingDocs = @("AGENTS.md", "CLAUDE.md", "ARCHITECTURE.md", "SECURITY.md", "tasks\current\task.md")

# Files that are excluded from the generic reports/ collection pass but are
# EXPLICITLY, individually allowlisted to be embedded as review evidence
# anyway (see Add-EvidenceSection). A file appearing here must never also
# appear in the final omitted list - that self-contradiction is exactly
# what the dedup pass inside Build-ReviewPacket prevents.
$securityCriticalHints = @(
    "core/identity.py", "core/authorization.py", "core/tenant_context.py",
    "core/config.py", "core/errors.py", "core/background_context.py", "core/audit.py"
)

# The auth API integration-test suite (split from a single large
# test_auth_api.py specifically because that file, at ~1200 lines, was
# large enough to be dropped by the size-budget logic below - see
# Build-ReviewPacket's droppable-sections pass). These are the primary
# end-to-end evidence for MED-004 auth behavior (login/logout, cookies,
# CSRF, clinic selection, stale-session handling); Codex has twice
# reported REVIEW_INCOMPLETE when this coverage was silently omitted, so
# every file here is pinned to Tier 1 (never dropped) rather than left to
# compete with other Tier 3 sections on size.
$authTestCoverageHints = @(
    "tests/integration/test_auth_login_logout_api.py",
    "tests/integration/test_auth_session_api.py",
    "tests/integration/test_auth_tenant_route_stale_cookie_api.py",
    "tests/integration/test_auth_clinic_selection_api.py",
    "tests/integration/test_auth_csrf_api.py",
    "tests/integration/test_auth_password_change_api.py",
    "tests/integration/test_auth_password_reset_api.py",
    "tests/integration/test_auth_invitation_api.py",
    "tests/integration/test_auth_dev_identity_api.py",
    "tests/integration/auth_api_helpers.py",
    "tests/integration/conftest.py",
    "tests/integration/test_password_reset_service.py",
    "tests/integration/test_session_service.py",
    "tests/integration/test_invitation_service.py"
)

# Top-level directories whose untracked/changed files are always candidates
# for full-content review.
$includedTopLevelDirs = @("backend", "frontend", ".ai-workflow", ".vscode")
# `reports/` is excluded from this generic loop on purpose: it holds
# generated output (including this very script's own packet/prompt/raw/
# stderr/JSON/review-latest.md) which must never recursively include
# itself. The two report files that ARE genuine review evidence
# (quality-gates-latest.md, migration-validation-latest.md) get their own
# dedicated sections elsewhere in Build-ReviewPacket, not via this generic
# loop.
$excludedTopLevelDirs = @("reports")
# Root-level (no directory segment) files worth reviewing by extension or
# exact name.
$includedRootExtensions = @(".md", ".yml", ".yaml", ".toml", ".json")
$includedRootFilenames = @("docker-compose.yml", "docker-compose.override.yml", ".env.example", ".gitignore")

function ConvertTo-NormalizedReviewPath {
    # Canonical form used ONLY for set-membership comparison (never for
    # display - callers keep the original path for that). Collapses the
    # differences that would otherwise defeat the already-embedded dedup
    # below: backslash vs forward-slash (Windows `git`/PowerShell paths),
    # a redundant leading "./", and casing (Windows paths are
    # case-insensitive, so "Task.md" and "task.md" must dedup as the same
    # file even though Git itself is case-sensitive about it).
    param([string]$Path)
    $normalized = ($Path -replace '\\', '/').Trim('/')
    while ($normalized.StartsWith('./')) { $normalized = $normalized.Substring(2) }
    return $normalized.ToLowerInvariant()
}

function Remove-AlreadyEmbeddedOmissions {
    # A path currently embedded in $Sections (a governing document,
    # allowlisted evidence, or tracked/untracked file content) must never
    # also appear in $Omitted. The per-loop Add-Omitted calls in
    # Build-ReviewPacket only see one path at a time and have no way to
    # know a governing document (e.g. tasks/current/task.md) was already
    # embedded via its own dedicated section before the generic
    # tracked-file diff loop encounters that same path again and rejects
    # it as "not under a reviewed directory". Mutates $Omitted in place
    # (Clear + re-add) so every reference to the same List object -
    # including the one already captured in a returned packet hashtable -
    # sees the correction.
    param(
        [System.Collections.Generic.List[hashtable]]$Sections,
        [System.Collections.Generic.List[hashtable]]$Omitted
    )
    $embedded = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($s in $Sections) {
        if ($s.Path) { [void]$embedded.Add((ConvertTo-NormalizedReviewPath $s.Path)) }
    }
    $kept = [System.Collections.Generic.List[hashtable]]::new()
    foreach ($o in $Omitted) {
        if (-not $embedded.Contains((ConvertTo-NormalizedReviewPath $o.Path))) {
            $kept.Add($o)
        }
    }
    $Omitted.Clear()
    foreach ($k in $kept) { $Omitted.Add($k) }
}

function Get-FenceLang {
    param([string]$Path)
    switch ([System.IO.Path]::GetExtension($Path).ToLowerInvariant()) {
        ".py"   { return "python" }
        ".json" { return "json" }
        ".md"   { return "markdown" }
        ".ps1"  { return "powershell" }
        ".ts"   { return "typescript" }
        ".tsx"  { return "tsx" }
        ".yml"  { return "yaml" }
        ".yaml" { return "yaml" }
        default { return "text" }
    }
}

function Test-ReviewRelevantPath {
    param([string]$RelNorm)
    $segments = $RelNorm -split '/'
    $topDir = $segments[0]

    if ($excludedTopLevelDirs -contains $topDir) { return $false }
    if ($includedTopLevelDirs -contains $topDir) { return $true }

    if ($segments.Count -eq 1) {
        if ($includedRootFilenames -contains $RelNorm) { return $true }
        $ext = [System.IO.Path]::GetExtension($RelNorm).ToLowerInvariant()
        if ($includedRootExtensions -contains $ext) { return $true }
    }

    return $false
}

function Build-ReviewPacket {
    <#
      Single source of truth for packet construction - used both by a real
      review run (against $root) and by -ValidateDiffCollection (against a
      disposable temporary repository). Never invokes Codex, never writes
      packet/prompt files, and never mutates the target repository's Git
      index or working tree - every Git command it runs is a read-only
      `status`/`diff`/`ls-files`/`rev-parse`/`merge-base`, addressed via
      `git -C $RepoRoot` rather than a process-wide `Set-Location`, so
      calling it against a disposable repo can never affect this repo (or
      the caller's current directory) and vice versa.
    #>
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [string[]]$ProtectedBranches = @()
    )

    $sections = [System.Collections.Generic.List[hashtable]]::new()
    $omitted = [System.Collections.Generic.List[hashtable]]::new()
    $reviewedCandidates = [System.Collections.Generic.List[string]]::new()
    $embeddedEvidence = [System.Collections.Generic.List[hashtable]]::new()
    $deletedFiles = [System.Collections.Generic.List[string]]::new()
    $renamedFiles = [System.Collections.Generic.List[hashtable]]::new()

    function Add-Section {
        param([string]$Title, [string]$RelPath, [string]$Content, [int]$Tier)
        $sections.Add(@{ Title = $Title; Path = $RelPath; Content = $Content; Tier = $Tier })
    }

    function Add-Omitted {
        param([string]$RelPath, [string]$Reason)
        $omitted.Add(@{ Path = $RelPath; Reason = $Reason })
    }

    function Add-EvidenceSection {
        param([string]$RelPath, [string]$Title, [string]$Content, [int]$Tier, [string]$Description)
        if ($EvidenceAllowlist -notcontains $RelPath) {
            throw "Add-EvidenceSection called with '$RelPath', which is not in `$EvidenceAllowlist. Add it to the allowlist first - this function must never become a generic 'embed anything' escape hatch."
        }
        Add-Section -Title $Title -RelPath $RelPath -Content $Content -Tier $Tier
        $reviewedCandidates.Add($RelPath)
        $embeddedEvidence.Add(@{ Path = $RelPath; Description = $Description })
    }

    # --- Mode selection: uncommitted work relative to HEAD, or a clean tree
    # falling back to the committed range against a protected base branch.
    # `git status --short` (not a diff) inherently reports staged AND
    # unstaged changes, so this decision was never the part that missed
    # staged edits - only the diff/content COLLECTION below was. Generated
    # reports/ noise must never force uncommitted mode on its own, hence the
    # separate exclude-reports status check.
    $statusShort = ((& git -C $RepoRoot status --short) | Out-String).Trim()
    $statusShortExcludingReports = (
        (& git -C $RepoRoot status --short -- . ":(exclude)reports") | Out-String
    ).Trim()

    $mode = "uncommitted"
    $diffRangeArg = @("HEAD")
    $nothingToReviewReason = $null

    if ([string]::IsNullOrWhiteSpace($statusShortExcludingReports)) {
        $baseBranch = $null
        foreach ($candidate in $ProtectedBranches) {
            & git -C $RepoRoot rev-parse --verify --quiet $candidate *>$null
            if ($LASTEXITCODE -eq 0) { $baseBranch = $candidate; break }
        }
        if (-not $baseBranch) {
            return @{
                Mode = "nothing-to-review"
                NothingToReviewReason = "No uncommitted changes and no protected base branch found locally - nothing to review."
                DiffRangeArg = @(); StatusShort = $statusShort; DiffStat = ""
                Sections = $sections; Omitted = $omitted; EmbeddedEvidence = $embeddedEvidence
                ReviewedCandidates = $reviewedCandidates; Deleted = $deletedFiles; Renamed = $renamedFiles
                MissingGoverningDocsCount = 0; GoverningDocsCount = $governingDocs.Count
            }
        }
        $mergeBase = ((& git -C $RepoRoot merge-base HEAD $baseBranch) | Out-String).Trim()
        $headRev = ((& git -C $RepoRoot rev-parse HEAD) | Out-String).Trim()
        if ($mergeBase -eq $headRev) {
            return @{
                Mode = "nothing-to-review"
                NothingToReviewReason = "No uncommitted changes and no commits ahead of '$baseBranch' - nothing to review."
                DiffRangeArg = @(); StatusShort = $statusShort; DiffStat = ""
                Sections = $sections; Omitted = $omitted; EmbeddedEvidence = $embeddedEvidence
                ReviewedCandidates = $reviewedCandidates; Deleted = $deletedFiles; Renamed = $renamedFiles
                MissingGoverningDocsCount = 0; GoverningDocsCount = $governingDocs.Count
            }
        }
        $diffRangeArg = @("$mergeBase..HEAD")
        $mode = "clean-committed-range"
    }

    # --- Tier 1: governing docs + task ------------------------------------------------
    $missingGoverningDocs = [System.Collections.Generic.List[string]]::new()
    foreach ($doc in $governingDocs) {
        $full = Join-Path $RepoRoot $doc
        $displayPath = $doc -replace '\\', '/'
        if (Test-Path $full) {
            $content = Get-Content -Raw -Encoding utf8 -Path $full
            Add-Section -Title "Governing document: $displayPath" -RelPath $displayPath `
                -Content "$fence$(Get-FenceLang $doc)`n$content`n$fence" -Tier 1
            $reviewedCandidates.Add($displayPath)
        } else {
            Add-Section -Title "Governing document: $displayPath" -RelPath $displayPath `
                -Content "NOT FOUND at $displayPath." -Tier 1
            $missingGoverningDocs.Add($displayPath)
            Add-Omitted -RelPath $displayPath -Reason "not found on disk"
        }
    }

    # --- Tier 1: git status / diff stat ------------------------------------------------
    Add-Section -Title "git status --short" -RelPath "(git status --short)" `
        -Content "$fence`n$statusShort`n$fence" -Tier 1

    $diffStat = ((& git -C $RepoRoot diff --stat -M @diffRangeArg) | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($diffStat)) { $diffStat = "(no diff in range - all changes are untracked new files; see below)" }
    Add-Section -Title "git diff --stat -M $diffRangeArg" -RelPath "(git diff --stat)" `
        -Content "$fence`n$diffStat`n$fence" -Tier 1

    # --- Tier 2: full unified diff, relative to the SAME baseline used for
    # the file list below - staged and unstaged tracked changes combined,
    # never a bare working-tree-vs-index diff. ------------------------------------------------
    $baselineDescription = if ($mode -eq "uncommitted") { "HEAD (staged + unstaged combined)" } else { "the merge-base commit" }
    $fullDiffLines = & git -C $RepoRoot diff --no-ext-diff -M --unified=80 @diffRangeArg
    $fullDiff = ($fullDiffLines | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($fullDiff)) { $fullDiff = "(empty - no tracked-file changes; all changes are untracked new files, see below)" }
    Add-Section -Title "git diff --no-ext-diff -M --unified=80 $diffRangeArg (tracked-file changes, relative to $baselineDescription)" -RelPath "(git diff)" `
        -Content "$fence`ndiff`n$fullDiff`n$fence" -Tier 2

    # --- Untracked files: enumerate, then split into relevant/excluded ------------------
    $untrackedAll = @(& git -C $RepoRoot ls-files --others --exclude-standard)
    $untrackedList = ($untrackedAll | Sort-Object)
    $untrackedListing = if ($untrackedList.Count -gt 0) { ($untrackedList -join "`n") } else { "(none)" }
    Add-Section -Title "Untracked files (all, names only)" -RelPath "(git ls-files --others)" `
        -Content "$fence`n$untrackedListing`n$fence" -Tier 1

    $relevantFiles = [System.Collections.Generic.List[string]]::new()
    foreach ($rel in $untrackedList) {
        $relNorm = $rel -replace '\\', '/'

        if (-not (Test-PathInsideRepoRoot -Root $RepoRoot -RelativePath $rel)) {
            Add-Omitted -RelPath $relNorm -Reason "outside repository root - refused"
            continue
        }
        if (Test-ExcludedRelativePath -RelativePath $relNorm) {
            Add-Omitted -RelPath $relNorm -Reason "excluded: secret/credential/build-artifact/binary-type path"
            continue
        }
        if (-not (Test-ReviewRelevantPath -RelNorm $relNorm)) {
            Add-Omitted -RelPath $relNorm -Reason "not under a reviewed directory or a recognized root config/doc type"
            continue
        }

        $fullPath = Join-Path $RepoRoot $rel
        if (-not (Test-Path $fullPath -PathType Leaf)) {
            Add-Omitted -RelPath $relNorm -Reason "listed as untracked but not readable as a file"
            continue
        }
        if (Test-BinaryFile -Path $fullPath) {
            Add-Omitted -RelPath $relNorm -Reason "binary file - not embedded as text"
            continue
        }

        $relevantFiles.Add($relNorm)
    }

    foreach ($relNorm in $relevantFiles) {
        $fullPath = Join-Path $RepoRoot $relNorm
        $content = Get-Content -Raw -Encoding utf8 -Path $fullPath
        $tier = 3
        foreach ($hint in ($securityCriticalHints + $authTestCoverageHints)) {
            if ($relNorm.EndsWith($hint)) { $tier = 1; break }
        }
        Add-Section -Title "Untracked file: $relNorm" -RelPath $relNorm `
            -Content "$fence$(Get-FenceLang $relNorm)`n$content`n$fence" -Tier $tier
        if (-not $reviewedCandidates.Contains($relNorm)) { $reviewedCandidates.Add($relNorm) }
    }

    # --- Tracked changes: ONE pass over `git diff --name-status -M`
    # relative to the same $diffRangeArg baseline as the diff/stat above.
    # This is the fix for the staged-change omission: --name-status HEAD
    # (or --name-status <mergeBase>..HEAD) reports the full working-tree
    # state - additions, modifications, deletions, and renames, combining
    # any staged and unstaged layers on a given file into that file's one
    # final entry - never a separate `git diff --cached` pass concatenated
    # with a separate `git diff` pass, which could represent (or omit) a
    # mixed-state file inconsistently. ------------------------------------------------
    $nameStatusLines = @(& git -C $RepoRoot diff --name-status -M @diffRangeArg)
    foreach ($line in $nameStatusLines) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        $fields = $line -split "`t"
        $statusCode = $fields[0]

        if ($statusCode.StartsWith("R") -or $statusCode.StartsWith("C")) {
            $oldRel = $fields[1] -replace '\\', '/'
            $newRel = $fields[2] -replace '\\', '/'
            $kind = if ($statusCode.StartsWith("R")) { "renamed" } else { "copied" }

            if (-not (Test-PathInsideRepoRoot -Root $RepoRoot -RelativePath $fields[2])) {
                Add-Omitted -RelPath $newRel -Reason "outside repository root - refused"
                continue
            }
            if (Test-ExcludedRelativePath -RelativePath $newRel) {
                Add-Omitted -RelPath $newRel -Reason "excluded: secret/credential/build-artifact/binary-type path"
                continue
            }
            if (-not (Test-ReviewRelevantPath -RelNorm $newRel)) {
                Add-Omitted -RelPath $newRel -Reason "not under a reviewed directory or a recognized root config/doc type"
                continue
            }

            $renamedFiles.Add(@{ OldPath = $oldRel; NewPath = $newRel; Status = $statusCode })

            $fullPath = Join-Path $RepoRoot $fields[2]
            if (-not (Test-Path $fullPath -PathType Leaf)) {
                Add-Omitted -RelPath $newRel -Reason "listed as $kind but not readable as a file"
                continue
            }
            if (Test-BinaryFile -Path $fullPath) {
                Add-Omitted -RelPath $newRel -Reason "binary file - not embedded as text ($kind from $oldRel)"
                continue
            }

            $content = Get-Content -Raw -Encoding utf8 -Path $fullPath
            $tier = 3
            foreach ($hint in ($securityCriticalHints + $authTestCoverageHints)) {
                if ($newRel.EndsWith($hint)) { $tier = 1; break }
            }
            Add-Section -Title "Tracked $kind file (full current content): $oldRel -> $newRel ($statusCode)" -RelPath $newRel `
                -Content "$fence$(Get-FenceLang $newRel)`n$content`n$fence" -Tier $tier
            if (-not $reviewedCandidates.Contains($newRel)) { $reviewedCandidates.Add($newRel) }
            continue
        }

        $rel = $fields[1]
        $relNorm = $rel -replace '\\', '/'

        if ($statusCode -eq "D") {
            # Deleted files have no on-disk content to embed - the removed
            # content is already fully visible in the unified diff above.
            # This is metadata, not an omission (it WAS reviewed, via the
            # diff), so it never goes into $omitted.
            if (-not (Test-ExcludedRelativePath -RelativePath $relNorm)) {
                $deletedFiles.Add($relNorm)
            }
            continue
        }

        if (-not (Test-PathInsideRepoRoot -Root $RepoRoot -RelativePath $rel)) {
            Add-Omitted -RelPath $relNorm -Reason "outside repository root - refused"
            continue
        }
        if (Test-ExcludedRelativePath -RelativePath $relNorm) {
            Add-Omitted -RelPath $relNorm -Reason "excluded: secret/credential/build-artifact/binary-type path"
            continue
        }
        if (-not (Test-ReviewRelevantPath -RelNorm $relNorm)) {
            Add-Omitted -RelPath $relNorm -Reason "not under a reviewed directory or a recognized root config/doc type"
            continue
        }

        $fullPath = Join-Path $RepoRoot $rel
        if (-not (Test-Path $fullPath -PathType Leaf)) {
            Add-Omitted -RelPath $relNorm -Reason "listed as changed ($statusCode) but not readable as a file - possibly deleted"
            continue
        }
        if (Test-BinaryFile -Path $fullPath) {
            Add-Omitted -RelPath $relNorm -Reason "binary file - not embedded as text"
            continue
        }

        $content = Get-Content -Raw -Encoding utf8 -Path $fullPath
        $tier = 3
        foreach ($hint in ($securityCriticalHints + $authTestCoverageHints)) {
            if ($relNorm.EndsWith($hint)) { $tier = 1; break }
        }
        Add-Section -Title "Tracked changed file ($statusCode, full current content - staged+unstaged combined): $relNorm" -RelPath $relNorm `
            -Content "$fence$(Get-FenceLang $relNorm)`n$content`n$fence" -Tier $tier
        if (-not $reviewedCandidates.Contains($relNorm)) { $reviewedCandidates.Add($relNorm) }
    }

    if ($deletedFiles.Count -gt 0) {
        $deletedListing = (($deletedFiles | Sort-Object -Unique)) -join "`n"
        Add-Section -Title "Deleted tracked files (removed relative to $baselineDescription)" -RelPath "(deleted files)" `
            -Content "$fence`n$deletedListing`n$fence" -Tier 1
    }
    if ($renamedFiles.Count -gt 0) {
        $renameListing = ($renamedFiles | ForEach-Object { "$($_.Status): $($_.OldPath) -> $($_.NewPath)" }) -join "`n"
        Add-Section -Title "Renamed/copied tracked files" -RelPath "(renamed files)" `
            -Content "$fence`n$renameListing`n$fence" -Tier 1
    }

    # --- Tier 1: quality-gate report / test output / migration / completion report -----
    # Both paths here are in $EvidenceAllowlist - embedded via Add-EvidenceSection
    # so they are never also left in the final omitted list (see the dedup pass
    # below), even though the generic reports/ collection pass above may have
    # already recorded them there.
    $qualityGatePath = Join-Path $RepoRoot "reports\quality-gates-latest.md"
    if (Test-Path $qualityGatePath) {
        $qgContent = Get-Content -Raw -Encoding utf8 -Path $qualityGatePath
        Add-EvidenceSection -RelPath "reports/quality-gates-latest.md" `
            -Title "Latest quality-gate report (reports/quality-gates-latest.md)" `
            -Content "$fence`n$qgContent`n$fence" -Tier 1 `
            -Description "Latest official quality-gate run output (ruff/mypy/pytest/frontend/Docker checks)."
        Add-Section -Title "Test output" -RelPath "(test output)" `
            -Content "Included within the quality-gate report above (see the backend-pytest section)." -Tier 1
    } else {
        Add-Section -Title "Latest quality-gate report" -RelPath "reports/quality-gates-latest.md" `
            -Content "NOT AVAILABLE - reports/quality-gates-latest.md does not exist." -Tier 1
        Add-Section -Title "Test output" -RelPath "(test output)" `
            -Content "NOT AVAILABLE - no quality-gate report to source it from." -Tier 1
        Add-Omitted -RelPath "reports/quality-gates-latest.md" -Reason "not found"
    }

    $migrationReportPath = Join-Path $RepoRoot "reports\migration-validation-latest.md"
    if (Test-Path $migrationReportPath) {
        $migrationContent = Get-Content -Raw -Encoding utf8 -Path $migrationReportPath
        Add-EvidenceSection -RelPath "reports/migration-validation-latest.md" `
            -Title "Migration upgrade/downgrade output (reports/migration-validation-latest.md)" `
            -Content "$fence`n$migrationContent`n$fence" -Tier 1 `
            -Description "Alembic upgrade/downgrade/re-upgrade validation transcript."
    } else {
        Add-Section -Title "Migration upgrade/downgrade output" -RelPath "(migration validation output)" `
            -Content "NOT AVAILABLE - reports/migration-validation-latest.md does not exist." -Tier 1
        Add-Omitted -RelPath "reports/migration-validation-latest.md" -Reason "not found"
    }

    Add-Section -Title "Claude completion report" -RelPath "(completion report)" `
        -Content "NOT AVAILABLE - the implementation completion report was communicated in the chat session and was not persisted to a file in this repository." -Tier 1

    # ---------------------------------------------------------------------------
    # Already-embedded dedup: ANY path currently embedded in $sections - not
    # just allowlisted evidence (reports/quality-gates-latest.md etc.), but
    # also governing documents (tasks/current/task.md, AGENTS.md, ...) and any
    # tracked/untracked file - must never also appear in the final omitted
    # list. The generic collection passes above only see one path at a time
    # and have no way to know, e.g., that a governing document was already
    # embedded via its own dedicated section before the tracked-file diff loop
    # encountered that same path again and rejected it as "not under a
    # reviewed directory" - this is what previously let a fully-reviewed
    # governing document still show up under "## Omitted files". Everything
    # this pass does NOT match - including this tooling's own self-referential
    # output (review-latest.md, codex-review-*.{md,txt,json}) - stays
    # correctly omitted.
    # ---------------------------------------------------------------------------
    Remove-AlreadyEmbeddedOmissions -Sections $sections -Omitted $omitted

    return @{
        Mode = $mode
        NothingToReviewReason = $nothingToReviewReason
        DiffRangeArg = $diffRangeArg
        StatusShort = $statusShort
        DiffStat = $diffStat
        Sections = $sections
        Omitted = $omitted
        EmbeddedEvidence = $embeddedEvidence
        ReviewedCandidates = $reviewedCandidates
        Deleted = $deletedFiles
        Renamed = $renamedFiles
        MissingGoverningDocsCount = $missingGoverningDocs.Count
        GoverningDocsCount = $governingDocs.Count
    }
}

# ===========================================================================
# -ValidateDiffCollection: exercises Build-ReviewPacket against a disposable
# Git repository under the OS temp directory. Never touches this repo's
# working tree, index, or branches - the only Git commands that ever run
# against $root elsewhere in this script are read-only status/diff/rev-parse
# calls, and this validation mode doesn't even reach that code path.
# ===========================================================================
if ($ValidateDiffCollection) {
    $failures = [System.Collections.Generic.List[string]]::new()

    # 13/14: Remove-AlreadyEmbeddedOmissions dedup, tested directly against
    # synthetic Sections/Omitted lists - independent of any real repo state,
    # so it exercises the exact backslash-vs-slash and casing-variant cases
    # the LOW finding's normalized-path requirement calls for.
    $dedupSections = [System.Collections.Generic.List[hashtable]]::new()
    $dedupSections.Add(@{ Path = "tasks/current/task.md"; Content = "embedded" })
    $dedupOmitted = [System.Collections.Generic.List[hashtable]]::new()
    $dedupOmitted.Add(@{ Path = "tasks\current\Task.md"; Reason = "not under a reviewed directory or a recognized root config/doc type" })
    $dedupOmitted.Add(@{ Path = "backend/tests/unit/test_rate_limit.py"; Reason = "dropped: packet exceeded the 1300000-character size budget" })
    Remove-AlreadyEmbeddedOmissions -Sections $dedupSections -Omitted $dedupOmitted
    if (@($dedupOmitted | Where-Object { (ConvertTo-NormalizedReviewPath $_.Path) -eq (ConvertTo-NormalizedReviewPath "tasks/current/task.md") }).Count -gt 0) {
        $failures.Add("Scenario 13: Remove-AlreadyEmbeddedOmissions did not dedup a backslash + casing variant of an already-embedded path.")
    }
    if (@($dedupOmitted | Where-Object { $_.Path -eq "backend/tests/unit/test_rate_limit.py" }).Count -ne 1) {
        $failures.Add("Scenario 14: Remove-AlreadyEmbeddedOmissions incorrectly removed a genuinely (non-embedded) omitted path.")
    }

    $tempRepo = Join-Path ([System.IO.Path]::GetTempPath()) ("review-packet-validation-" + [System.Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tempRepo | Out-Null

    try {
        & git -C $tempRepo init -q -b main *>$null
        & git -C $tempRepo config user.email "review-validation@example.invalid"
        & git -C $tempRepo config user.name "Review Packet Validation"

        Set-Content -Path (Join-Path $tempRepo "unstaged.md") -Value "original unstaged`n" -NoNewline
        Set-Content -Path (Join-Path $tempRepo "staged.md") -Value "original staged`n" -NoNewline
        Set-Content -Path (Join-Path $tempRepo "mixed.md") -Value "original mixed`n" -NoNewline
        Set-Content -Path (Join-Path $tempRepo "to-delete.md") -Value "will be deleted`n" -NoNewline
        Set-Content -Path (Join-Path $tempRepo "old-name.md") -Value "will be renamed`n" -NoNewline
        Set-Content -Path (Join-Path $tempRepo "README.md") -Value "# base`n" -NoNewline
        & git -C $tempRepo add -A *>$null
        & git -C $tempRepo commit -q -m "base" *>$null

        # 1: unstaged-only tracked change.
        Set-Content -Path (Join-Path $tempRepo "unstaged.md") -Value "changed unstaged`n" -NoNewline

        # 2: staged-only tracked change.
        Set-Content -Path (Join-Path $tempRepo "staged.md") -Value "changed staged`n" -NoNewline
        & git -C $tempRepo add "staged.md" *>$null

        # 3: one file with both staged and unstaged layers.
        Set-Content -Path (Join-Path $tempRepo "mixed.md") -Value "staged layer`n" -NoNewline
        & git -C $tempRepo add "mixed.md" *>$null
        Set-Content -Path (Join-Path $tempRepo "mixed.md") -Value "staged layer`nfinal unstaged layer`n" -NoNewline

        # 4: staged new tracked file.
        Set-Content -Path (Join-Path $tempRepo "new-staged.md") -Value "brand new`n" -NoNewline
        & git -C $tempRepo add "new-staged.md" *>$null

        # 5: staged deletion.
        & git -C $tempRepo rm -q "to-delete.md" *>$null

        # 6: staged rename.
        & git -C $tempRepo mv "old-name.md" "new-name.md" *>$null

        # 7: relevant untracked file.
        Set-Content -Path (Join-Path $tempRepo "untracked.md") -Value "untracked content`n" -NoNewline

        $statusBefore = ((& git -C $tempRepo status --porcelain=v2) | Out-String)

        $packet = Build-ReviewPacket -RepoRoot $tempRepo -ProtectedBranches @("main")

        # 12: packet construction never mutates the target repo's Git index
        # or working tree.
        $statusAfter = ((& git -C $tempRepo status --porcelain=v2) | Out-String)
        if ($statusBefore -ne $statusAfter) {
            $failures.Add("git status changed after building the packet - the index or working tree was modified.")
        }

        if ($packet.Mode -ne "uncommitted") {
            $failures.Add("Expected 'uncommitted' review mode for a dirty tree, got '$($packet.Mode)'.")
        }

        $paths = @($packet.ReviewedCandidates)

        # 1
        if ($paths -notcontains "unstaged.md") { $failures.Add("Scenario 1: unstaged-only change (unstaged.md) was not included.") }
        # 2
        if ($paths -notcontains "staged.md") { $failures.Add("Scenario 2: staged-only change (staged.md) was not included.") }
        # 3
        $mixedCount = @($paths | Where-Object { $_ -eq "mixed.md" }).Count
        if ($mixedCount -ne 1) { $failures.Add("Scenario 3: mixed.md should appear exactly once in reviewed candidates, found $mixedCount.") }
        $mixedSection = $packet.Sections | Where-Object { $_.Path -eq "mixed.md" } | Select-Object -First 1
        if (-not $mixedSection -or $mixedSection.Content -notmatch [regex]::Escape("final unstaged layer")) {
            $failures.Add("Scenario 3: mixed.md section does not contain the final working-tree content.")
        }
        # 4
        if ($paths -notcontains "new-staged.md") { $failures.Add("Scenario 4: staged new file (new-staged.md) was not included.") }
        # 5
        if (@($packet.Deleted) -notcontains "to-delete.md") { $failures.Add("Scenario 5: staged deletion (to-delete.md) was not represented as deleted.") }
        # 6
        $renameMatch = @($packet.Renamed | Where-Object { $_.OldPath -eq "old-name.md" -and $_.NewPath -eq "new-name.md" })
        if ($renameMatch.Count -eq 0) { $failures.Add("Scenario 6: staged rename (old-name.md -> new-name.md) was not represented.") }
        if ($paths -notcontains "new-name.md") { $failures.Add("Scenario 6: renamed file's new path (new-name.md) was not included as reviewed.") }
        # 7
        if ($paths -notcontains "untracked.md") { $failures.Add("Scenario 7: relevant untracked file (untracked.md) was not included.") }
        # 10
        $dupeGroups = @($paths | Group-Object | Where-Object { $_.Count -gt 1 })
        if ($dupeGroups.Count -gt 0) {
            $failures.Add("Scenario 10: duplicate reviewed-file entries: $(($dupeGroups | ForEach-Object { $_.Name }) -join ', ')")
        }
        # 11
        if ([string]::IsNullOrWhiteSpace($packet.DiffStat) -or $packet.DiffStat -notmatch "staged.md") {
            $failures.Add("Scenario 11: git diff --stat output does not reflect staged changes for the selected mode.")
        }

        # 8: settle everything into a commit, then add ONLY report-style
        # noise - must NOT switch back into uncommitted mode.
        & git -C $tempRepo add -A *>$null
        & git -C $tempRepo commit -q -m "settle disposable scenarios" *>$null
        New-Item -ItemType Directory -Path (Join-Path $tempRepo "reports") -Force | Out-Null
        Set-Content -Path (Join-Path $tempRepo "reports\generated.md") -Value "noise`n" -NoNewline

        $noisePacket = Build-ReviewPacket -RepoRoot $tempRepo -ProtectedBranches @("main")
        if ($noisePacket.Mode -eq "uncommitted") {
            $failures.Add("Scenario 8: generated reports/-only noise incorrectly triggered uncommitted mode.")
        }

        # 9: clean-tree committed-range fallback, exercised on a branch that
        # is genuinely ahead of a distinct base branch.
        Remove-Item -Recurse -Force (Join-Path $tempRepo "reports")
        & git -C $tempRepo branch -q base-for-range *>$null
        Set-Content -Path (Join-Path $tempRepo "ahead.md") -Value "ahead commit`n" -NoNewline
        & git -C $tempRepo add "ahead.md" *>$null
        & git -C $tempRepo commit -q -m "ahead of base" *>$null

        $rangePacket = Build-ReviewPacket -RepoRoot $tempRepo -ProtectedBranches @("base-for-range")
        if ($rangePacket.Mode -ne "clean-committed-range") {
            $failures.Add("Scenario 9: clean tree ahead of its base branch did not select the committed-range fallback (got '$($rangePacket.Mode)').")
        }
        if (@($rangePacket.ReviewedCandidates) -notcontains "ahead.md") {
            $failures.Add("Scenario 9: committed-range review did not include the committed file (ahead.md).")
        }
    } finally {
        Remove-Item -Recurse -Force $tempRepo -ErrorAction SilentlyContinue
    }

    if ($failures.Count -gt 0) {
        Write-Host "Diff-collection validation FAILED:"
        foreach ($f in $failures) { Write-Host "  - $f" }
        exit 1
    }

    Write-Host "Diff-collection validation PASSED (14 scenarios, disposable repo at $tempRepo, now removed)."
    exit 0
}

if (-not (Test-CommandAvailable "codex")) {
    Write-Error "codex CLI not found on PATH. Install with: npm install -g @openai/codex"
    exit 1
}

Set-Location $root

$config = Get-WorkflowConfig
$packet = Build-ReviewPacket -RepoRoot $root -ProtectedBranches $config.protectedBranches

if ($packet.Mode -eq "nothing-to-review") {
    Write-Host $packet.NothingToReviewReason
    exit 0
}
if ($packet.Mode -eq "clean-committed-range") {
    Write-Host "Working tree is clean - reviewing the committed range $($packet.DiffRangeArg[0]) instead."
}

$branch = (& git rev-parse --abbrev-ref HEAD).Trim()
$taskId = ($branch -replace '[\\/:*?"<>|]', '-')
$taskReportsDir = Join-Path $root "reports\$taskId"

$promptPath       = Join-Path $taskReportsDir "codex-review-prompt.md"
$packetPath       = Join-Path $taskReportsDir "codex-review-packet.md"
$rawOutputPath    = Join-Path $taskReportsDir "codex-review-raw.txt"
$jsonOutputPath   = Join-Path $taskReportsDir "codex-review.json"
$stderrPath       = Join-Path $taskReportsDir "codex-review-stderr.txt"
$schemaPath       = Join-Path $root ".ai-workflow\prompts\review-output-schema.json"

# ---------------------------------------------------------------------------
# Size handling: drop lowest-priority (highest Tier number) sections first,
# largest-first within a tier, until under budget. Never truncate a
# section's content - only drop it whole, and record it as omitted.
# 1,300,000 chars is a conservative budget well under typical large-context
# model limits while leaving headroom for the prompt/schema/instructions.
# Raised from 900,000 (MED-004 repair): on a clean tree the diff is always
# taken against the merge-base with master (see the diff-baseline comment
# near the top of this file), so a long-running feature branch with many
# repair commits keeps accreting legitimate review-relevant diff content
# round over round - splitting the one largest file (done for
# test_auth_api.py, see $authTestCoverageHints) stops that specific file
# from being the casualty, but does not stop the same size pressure from
# dropping a DIFFERENT file next round. Once the budget was no longer
# comfortably above the real size of a normal, non-bloated review packet
# for this branch, raising it was the right call over further hint-pinning
# individual files one at a time.
# ---------------------------------------------------------------------------
$MaxPacketChars = 1300000
$sections = $packet.Sections

function Get-TotalChars {
    param([System.Collections.Generic.List[hashtable]]$Secs)
    $total = 0
    foreach ($s in $Secs) { $total += $s.Content.Length }
    return $total
}

$totalChars = Get-TotalChars -Secs $sections
if ($totalChars -gt $MaxPacketChars) {
    $droppable = $sections | Where-Object { $_.Tier -gt 1 } | Sort-Object -Property Tier -Descending
    foreach ($candidate in $droppable) {
        if ($totalChars -le $MaxPacketChars) { break }
        $sections.Remove($candidate) | Out-Null
        $packet.Omitted.Add(@{ Path = $candidate.Path; Reason = "dropped: packet exceeded the $MaxPacketChars-character size budget" })
        $totalChars = Get-TotalChars -Secs $sections
    }
}

# --- Re-run the same already-embedded dedup after the size-drop loop
# above: dropping a section removes it from $sections, so a genuinely
# budget-dropped file is (correctly) not in $sections any more and stays
# listed as omitted - this call only catches the case where a section
# survives the drop but an earlier Add-Omitted call for that same
# (now-stale) reason was never cleaned up.
Remove-AlreadyEmbeddedOmissions -Sections $sections -Omitted $packet.Omitted

$finalTotalChars = Get-TotalChars -Secs $sections
if ($finalTotalChars -gt $MaxPacketChars -or $packet.MissingGoverningDocsCount -eq $packet.GoverningDocsCount) {
    $failLines = @(
        "# Codex review - FAILED (could not build a meaningful review packet)",
        "",
        "Reason: " + $(if ($finalTotalChars -gt $MaxPacketChars) {
            "even after dropping every droppable section, the packet is $finalTotalChars characters, over the $MaxPacketChars budget."
        } else {
            "none of the governing documents ($($governingDocs -join ', ')) could be found."
        }),
        "",
        "No Codex invocation was attempted. This is a tooling failure, not a review outcome."
    )
    Write-WorkflowReport -Name "review-latest.md" -Content ($failLines -join "`n")
    Write-Error "Cannot build a meaningful review packet - see reports/review-latest.md."
    exit 1
}

# ---------------------------------------------------------------------------
# Assemble and save the packet + prompt, then invoke Codex
# ---------------------------------------------------------------------------
$packetLines = @("# Review packet", "", "Generated for task branch `"$branch`".", "")
foreach ($s in ($sections | Sort-Object -Property Tier, Path)) {
    $packetLines += "## $($s.Title)"
    $packetLines += ""
    $packetLines += $s.Content
    $packetLines += ""
}
if ($packet.EmbeddedEvidence.Count -gt 0) {
    $packetLines += "## Embedded evidence files"
    $packetLines += ""
    $packetLines += "Excluded from the generic reports/ collection pass above, but explicitly"
    $packetLines += "allowlisted and embedded in full elsewhere in this packet as review"
    $packetLines += "evidence. These must NOT also appear under `"Omitted files`" below."
    $packetLines += ""
    foreach ($e in ($packet.EmbeddedEvidence | Sort-Object -Property Path)) {
        $packetLines += "- $($e.Path) - $($e.Description)"
    }
    $packetLines += ""
}
if ($packet.Omitted.Count -gt 0) {
    $packetLines += "## Omitted files"
    $packetLines += ""
    foreach ($o in ($packet.Omitted | Sort-Object -Property Path)) {
        $packetLines += "- $($o.Path) ($($o.Reason))"
    }
    $packetLines += ""
}
$packetContent = $packetLines -join "`n"
Write-Utf8File -Path $packetPath -Content $packetContent

$promptTemplate = Get-Content -Raw -Encoding utf8 -Path (Join-Path $root ".ai-workflow\prompts\review.md")
Write-Utf8File -Path $promptPath -Content $promptTemplate

if ($ValidateProvenance) {
    $failures = [System.Collections.Generic.List[string]]::new()
    $embeddedPathSet = @($packet.EmbeddedEvidence | ForEach-Object { $_.Path })
    $omittedPathSet = @($packet.Omitted | ForEach-Object { $_.Path })
    $selfReferential = @(
        "reports/review-latest.md",
        "$taskId/codex-review-packet.md", "$taskId/codex-review-prompt.md",
        "$taskId/codex-review-raw.txt", "$taskId/codex-review-stderr.txt",
        "$taskId/codex-review.json"
    )

    # 1 & 5: generic report noise and this tooling's own output stay excluded.
    foreach ($p in $selfReferential) {
        $embeddedMatch = @($embeddedPathSet | Where-Object { $_ -like "*$p" -or $_ -eq $p })
        if ($embeddedMatch.Count -gt 0) {
            $failures.Add("Self-referential/generic path '$p' was embedded as evidence - it must never be.")
        }
    }

    # 2 & 3: migration evidence embedded when present, and never also omitted.
    if (Test-Path (Join-Path $root "reports\migration-validation-latest.md")) {
        if ($embeddedPathSet -notcontains "reports/migration-validation-latest.md") {
            $failures.Add("reports/migration-validation-latest.md exists but was not embedded as evidence.")
        }
        if ($omittedPathSet -contains "reports/migration-validation-latest.md") {
            $failures.Add("reports/migration-validation-latest.md is both embedded as evidence and listed as omitted.")
        }
    }

    # 4: quality-gate evidence follows the same rule.
    if (Test-Path (Join-Path $root "reports\quality-gates-latest.md")) {
        if ($embeddedPathSet -notcontains "reports/quality-gates-latest.md") {
            $failures.Add("reports/quality-gates-latest.md exists but was not embedded as evidence.")
        }
        if ($omittedPathSet -contains "reports/quality-gates-latest.md") {
            $failures.Add("reports/quality-gates-latest.md is both embedded as evidence and listed as omitted.")
        }
    }

    # 6: no path appears in both embedded-evidence and omitted, for any path.
    $overlap = @($embeddedPathSet | Where-Object { $omittedPathSet -contains $_ })
    if ($overlap.Count -gt 0) {
        $failures.Add("Path(s) appear in both embedded-evidence and omitted: $($overlap -join ', ')")
    }

    # 8: every governing document that exists on disk is embedded exactly
    # once in $sections (as its own "Governing document: ..." section) and
    # never also appears under "## Omitted files" - this is the exact
    # contradiction the LOW finding reported for tasks/current/task.md.
    $normalizedOmitted = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($p in $omittedPathSet) { [void]$normalizedOmitted.Add((ConvertTo-NormalizedReviewPath $p)) }
    foreach ($doc in $governingDocs) {
        $full = Join-Path $root $doc
        if (-not (Test-Path $full)) { continue }
        $displayPath = $doc -replace '\\', '/'
        $matchingSections = @($packet.Sections | Where-Object {
            (ConvertTo-NormalizedReviewPath $_.Path) -eq (ConvertTo-NormalizedReviewPath $displayPath)
        })
        if ($matchingSections.Count -ne 1) {
            $failures.Add("Governing document '$displayPath' is embedded $($matchingSections.Count) time(s); expected exactly 1.")
        }
        if ($normalizedOmitted.Contains((ConvertTo-NormalizedReviewPath $displayPath))) {
            $failures.Add("Governing document '$displayPath' is embedded but also appears under '## Omitted files'.")
        }
    }

    # 9: normalized-path dedup itself - a backslash path and its
    # forward-slash equivalent, and a casing variant, must compare equal.
    if ((ConvertTo-NormalizedReviewPath "tasks\current\task.md") -ne (ConvertTo-NormalizedReviewPath "tasks/current/task.md")) {
        $failures.Add("Path normalization does not dedup backslash vs forward-slash paths.")
    }
    if ((ConvertTo-NormalizedReviewPath "Tasks/Current/Task.md") -ne (ConvertTo-NormalizedReviewPath "tasks/current/task.md")) {
        $failures.Add("Path normalization does not dedup casing variants.")
    }
    if ((ConvertTo-NormalizedReviewPath "./tasks/current/task.md") -ne (ConvertTo-NormalizedReviewPath "tasks/current/task.md")) {
        $failures.Add("Path normalization does not strip a redundant leading './'.")
    }

    # 7: deterministic ordering - re-running Sort-Object on the same input
    # twice must produce identical output (the packet renders both lists via
    # `Sort-Object -Property Path`, so this is what "reproducible packet
    # ordering" actually reduces to).
    $sortedOnce = ($omittedPathSet | Sort-Object) -join "|"
    $sortedTwice = ($omittedPathSet | Sort-Object | Sort-Object) -join "|"
    if ($sortedOnce -ne $sortedTwice) {
        $failures.Add("Omitted-file ordering is not deterministic across repeated sorts.")
    }

    if ($failures.Count -gt 0) {
        Write-Host "Provenance validation FAILED:"
        foreach ($f in $failures) { Write-Host "  - $f" }
        exit 1
    }

    Write-Host "Provenance validation PASSED:"
    Write-Host "  - embedded evidence: $($embeddedPathSet -join ', ')"
    Write-Host "  - omitted: $($omittedPathSet.Count) path(s), none overlapping embedded evidence"
    Write-Host "  - self-referential/generic report paths correctly excluded from embedded evidence"
    exit 0
}

if ($DryRun) {
    Write-Host "Dry run: packet written to $packetPath ($finalTotalChars chars), prompt written to $promptPath."
    Write-Host "Reviewed-file candidates: $($packet.ReviewedCandidates.Count); omitted: $($packet.Omitted.Count)."
    Write-Host "Codex was NOT invoked (-DryRun)."
    exit 0
}

$stdinPath = Join-Path $taskReportsDir "codex-review-stdin.tmp.md"
Write-Utf8File -Path $stdinPath -Content ($promptTemplate + "`n`n" + $packetContent)

$codexArgs = @(
    "exec", "--sandbox", "read-only", "--skip-git-repo-check", "-C", $root,
    "--output-schema", $schemaPath,
    "--output-last-message", $rawOutputPath,
    "-"
)
$result = Invoke-CapturedCommand -FilePath "codex" -ArgumentList $codexArgs -StandardInputPath $stdinPath
Remove-Item $stdinPath -ErrorAction SilentlyContinue

Write-Utf8File -Path $stderrPath -Content ($result.Stderr ?? "")

# ---------------------------------------------------------------------------
# Validate the outcome - exit code 0 alone is never sufficient.
# ---------------------------------------------------------------------------
$failureReasons = [System.Collections.Generic.List[string]]::new()
$rawOutput = $null
$parsed = $null

if ($result.ExitCode -ne 0) {
    $failureReasons.Add("Codex process exited $($result.ExitCode).")
} elseif (-not (Test-Path $rawOutputPath)) {
    $failureReasons.Add("Codex exited 0 but did not write an output-last-message file.")
} else {
    $rawOutput = Get-Content -Raw -Encoding utf8 -Path $rawOutputPath
    if ([string]::IsNullOrWhiteSpace($rawOutput)) {
        $failureReasons.Add("Codex's last-message output file is empty.")
    } else {
        try {
            $parsed = $rawOutput | ConvertFrom-Json -ErrorAction Stop
        } catch {
            $failureReasons.Add("Codex output did not parse as valid JSON: $($_.Exception.Message)")
        }
    }
}

$suspiciousPhrases = @(
    "could not perform the review", "command execution is blocked",
    "createprocesswithlogonw", "windows sandbox", "no working shell session",
    "cannot access the repository", "could not inspect the",
    "i need one of these", "could not run"
)
if ($rawOutput) {
    foreach ($phrase in $suspiciousPhrases) {
        if ($rawOutput.ToLowerInvariant().Contains($phrase)) {
            $failureReasons.Add("Output contains a phrase indicating Codex could not inspect the repository ('$phrase').")
        }
    }
}

if ($parsed) {
    $allowedStatuses = @("APPROVED", "CHANGES_REQUIRED", "REJECTED", "REVIEW_INCOMPLETE")
    if (-not $allowedStatuses.Contains([string]$parsed.status)) {
        $failureReasons.Add("status '$($parsed.status)' is not one of $($allowedStatuses -join ', ').")
    }
    $reviewedFiles = @($parsed.reviewed_files)
    if ($reviewedFiles.Count -eq 0) {
        $failureReasons.Add("reviewed_files is empty.")
    }
    if ([string]$parsed.status -eq "REVIEW_INCOMPLETE") {
        $failureReasons.Add("Codex reported REVIEW_INCOMPLETE: $($parsed.summary)")
    }
}

$fence3 = [string]::new('`', 3)

if ($failureReasons.Count -gt 0) {
    $lines = @(
        "# Codex review - FAILED (no valid completed review)",
        "",
        "Command: $($result.Command)",
        "Prompt file: $promptPath",
        "Packet file: $packetPath",
        "Raw output file: $rawOutputPath",
        "Stderr file: $stderrPath",
        "Exit code: $($result.ExitCode)",
        "",
        "## Failure reasons",
        ""
    )
    foreach ($r in $failureReasons) { $lines += "- $r" }
    $lines += @(
        "",
        "## Raw output (if any)",
        $fence3,
        ($rawOutput ?? "(none)"),
        $fence3,
        "",
        "## Result",
        "No valid completed review was produced. Do not treat this as 'no findings' -",
        "it is a process/validation failure. See the failure reasons above."
    )
    Write-WorkflowReport -Name "review-latest.md" -Content ($lines -join "`n")
    if ($rawOutput) { Write-Utf8File -Path $jsonOutputPath -Content $rawOutput }
    Write-Error "Codex review did not produce a valid completed review. See reports/review-latest.md."
    exit 1
}

# Valid completed review from here on.
$prettyJson = $parsed | ConvertTo-Json -Depth 10
Write-Utf8File -Path $jsonOutputPath -Content $prettyJson

$lines = @(
    "# Codex review - completed",
    "",
    "Command: $($result.Command)",
    "Prompt file: $promptPath",
    "Packet file: $packetPath",
    "Raw output file: $rawOutputPath",
    "JSON output file: $jsonOutputPath",
    "Stderr file: $stderrPath",
    "Exit code: $($result.ExitCode)",
    "",
    "## Status: $($parsed.status)",
    "",
    "## Summary",
    $parsed.summary,
    "",
    "## Reviewed files ($(@($parsed.reviewed_files).Count))",
    ""
)
foreach ($f in @($parsed.reviewed_files)) { $lines += "- $f" }
$lines += @("", "## Omitted files (per Codex, $(@($parsed.omitted_files).Count))", "")
foreach ($f in @($parsed.omitted_files)) { $lines += "- $f" }
$lines += @("", "## Findings ($(@($parsed.findings).Count))", "")
if (@($parsed.findings).Count -eq 0) {
    $lines += "(none)"
} else {
    foreach ($finding in @($parsed.findings)) {
        $lines += "### [$($finding.severity)] $($finding.file) $(if ($finding.line) { ":$($finding.line)" })"
        $lines += "- Problem: $($finding.problem)"
        $lines += "- Impact: $($finding.impact)"
        $lines += "- Required fix: $($finding.required_fix)"
        if ($finding.required_test) { $lines += "- Required test: $($finding.required_test)" }
        $lines += ""
    }
}
$lines += @(
    "## Result",
    "Valid completed review (status: $($parsed.status)). This is a real review",
    "outcome, not merely a process exit code - stopping for human approval."
)
Write-WorkflowReport -Name "review-latest.md" -Content ($lines -join "`n")

Write-Host "Codex review completed (status: $($parsed.status)). See reports/review-latest.md - stopping for human approval."
