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
# -DryRun builds and saves the prompt + packet files (for inspection) and
# stops before invoking Codex at all - use this to check what would be sent
# without spending a Codex call.
#
# -ValidateProvenance builds the packet (like -DryRun) and then asserts the
# embedded-evidence/omitted-files provenance rules hold - no report file is
# ever both embedded as evidence and listed as omitted, generic reports/
# noise and this tooling's own self-referential output stay excluded, and
# the two allowlisted evidence files are embedded whenever present. Exits
# non-zero without invoking Codex if any assertion fails. This is the
# narrow, fixture-free validation mode for this provenance logic - there is
# no larger test harness for this PowerShell tooling to plug into.

param([switch]$DryRun, [switch]$ValidateProvenance)

. "$PSScriptRoot\common.ps1"

$root = Get-RepoRoot
Set-Location $root

if (-not (Test-CommandAvailable "codex")) {
    Write-Error "codex CLI not found on PATH. Install with: npm install -g @openai/codex"
    exit 1
}

$statusShort = (& git status --short | Out-String).Trim()

# reports/ holds only this tooling's own generated output (quality-gate and
# review transcripts) - never real source - so it must not count as "there
# are pending changes to review" on its own. Without this, a genuinely
# clean source tree right after finalizing a task into commits would still
# look "dirty" purely from these generated files and never take the
# committed-range fallback below.
$statusShortExcludingReports = (
    (& git status --short -- . ":(exclude)reports") | Out-String
).Trim()

# $diffRangeArg is empty while there are uncommitted SOURCE changes (the
# original, most common case: review pending work-in-progress against the
# working tree/index). If those are clean - e.g. right after finalizing a
# task into commits - fall back to reviewing this branch's committed,
# not-yet-merged range against its base branch, so "commit, then review"
# still produces a real review instead of an empty packet with nothing to
# diff.
$diffRangeArg = @()
if ([string]::IsNullOrWhiteSpace($statusShortExcludingReports)) {
    $config = Get-WorkflowConfig
    $baseBranch = $null
    foreach ($candidate in $config.protectedBranches) {
        & git rev-parse --verify --quiet $candidate *>$null
        if ($LASTEXITCODE -eq 0) { $baseBranch = $candidate; break }
    }
    if (-not $baseBranch) {
        Write-Host "No uncommitted changes and no protected base branch found locally - nothing to review."
        exit 0
    }
    $mergeBase = (& git merge-base HEAD $baseBranch).Trim()
    $headRev = (& git rev-parse HEAD).Trim()
    if ($mergeBase -eq $headRev) {
        Write-Host "No uncommitted changes and no commits ahead of '$baseBranch' - nothing to review."
        exit 0
    }
    $diffRangeArg = @("$mergeBase..HEAD")
    Write-Host "Working tree is clean - reviewing the committed range $mergeBase..HEAD against '$baseBranch' instead."
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
# Packet assembly helpers
# ---------------------------------------------------------------------------

# Sections are dropped in size-pressure order lowest-priority-first:
# Tier 1 = never dropped (governing docs/task/status/diff-stat/reports);
# Tier 2 = full unified diff;
# Tier 3 = untracked tenancy-relevant file contents.
# 900,000 chars is a conservative budget well under typical large-context
# model limits while leaving headroom for the prompt/schema/instructions.
$MaxPacketChars = 900000

$sections = [System.Collections.Generic.List[hashtable]]::new()
# Structured, not plain strings, so an entry can be reliably identified and
# removed by Path later (see the evidence-vs-omitted dedup pass below) -
# rendering to "path (reason)" text happens only at the very end.
$omitted = [System.Collections.Generic.List[hashtable]]::new()
$reviewedCandidates = [System.Collections.Generic.List[string]]::new()
# Files that are excluded from the generic reports/ collection pass but are
# EXPLICITLY, individually allowlisted to be embedded as review evidence
# anyway (see Add-EvidenceSection). A file appearing here must never also
# appear in the final omitted list - that self-contradiction is exactly
# what this structure prevents.
$embeddedEvidence = [System.Collections.Generic.List[hashtable]]::new()

function Add-Section {
    param([string]$Title, [string]$RelPath, [string]$Content, [int]$Tier)
    $sections.Add(@{ Title = $Title; Path = $RelPath; Content = $Content; Tier = $Tier })
}

function Add-Omitted {
    param([string]$RelPath, [string]$Reason)
    $omitted.Add(@{ Path = $RelPath; Reason = $Reason })
}

function Add-EvidenceSection {
    <#
      The ONLY sanctioned way to embed a file that lives under reports/.
      RelPath must be one of $EvidenceAllowlist (checked by the caller) -
      this function does not itself enforce that, so it stays a single,
      auditable call site rather than a generic "embed anything" escape
      hatch. Marks RelPath as reviewed AND as embedded evidence; the
      dedup pass after all sections are built removes it from $omitted
      even if the generic reports/ exclusion pass added it there first.
    #>
    param([string]$RelPath, [string]$Title, [string]$Content, [int]$Tier, [string]$Description)
    Add-Section -Title $Title -RelPath $RelPath -Content $Content -Tier $Tier
    $reviewedCandidates.Add($RelPath)
    $embeddedEvidence.Add(@{ Path = $RelPath; Description = $Description })
}

# Fixed, explicit allowlist - never a pattern/wildcard - of the only
# reports/ files ever permitted to be embedded as evidence despite the
# general reports/ exclusion. Adding a new evidence file requires editing
# this list AND adding its own Add-EvidenceSection call site, so arbitrary
# files under reports/ can never bypass exclusion implicitly.
$EvidenceAllowlist = @("reports/quality-gates-latest.md", "reports/migration-validation-latest.md")

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

function Read-GoverningDoc {
    param([string]$RelPath)
    $full = Join-Path $root $RelPath
    if (Test-Path $full) {
        return (Get-Content -Raw -Encoding utf8 -Path $full)
    }
    return $null
}

$fence = [string]::new('`', 3)

# --- Tier 1: governing docs + task ------------------------------------------------
$governingDocs = @("AGENTS.md", "CLAUDE.md", "ARCHITECTURE.md", "SECURITY.md", "tasks\current\task.md")
$missingGoverningDocs = [System.Collections.Generic.List[string]]::new()
foreach ($doc in $governingDocs) {
    $content = Read-GoverningDoc -RelPath $doc
    $displayPath = $doc -replace '\\', '/'
    if ($null -eq $content) {
        Add-Section -Title "Governing document: $displayPath" -RelPath $displayPath `
            -Content "NOT FOUND at $displayPath." -Tier 1
        $missingGoverningDocs.Add($displayPath)
        Add-Omitted -RelPath $displayPath -Reason "not found on disk"
    } else {
        Add-Section -Title "Governing document: $displayPath" -RelPath $displayPath `
            -Content "$fence$(Get-FenceLang $doc)`n$content`n$fence" -Tier 1
        $reviewedCandidates.Add($displayPath)
    }
}

# --- Tier 1: git status / diff stat ------------------------------------------------
Add-Section -Title "git status --short" -RelPath "(git status --short)" `
    -Content "$fence`n$statusShort`n$fence" -Tier 1

$diffStat = ((& git diff --stat @diffRangeArg) | Out-String).Trim()
if ([string]::IsNullOrWhiteSpace($diffStat)) { $diffStat = "(no diff in range - all changes are untracked new files; see below)" }
Add-Section -Title "git diff --stat $diffRangeArg" -RelPath "(git diff --stat)" `
    -Content "$fence`n$diffStat`n$fence" -Tier 1

# --- Tier 2: full unified diff ------------------------------------------------
$fullDiffLines = & git diff --no-ext-diff --unified=80 @diffRangeArg
$fullDiff = ($fullDiffLines | Out-String).Trim()
if ([string]::IsNullOrWhiteSpace($fullDiff)) { $fullDiff = "(empty - no tracked-file changes; all changes are untracked new files, see below)" }
Add-Section -Title "git diff --no-ext-diff --unified=80 $diffRangeArg (tracked-file changes)" -RelPath "(git diff)" `
    -Content "$fence`ndiff`n$fullDiff`n$fence" -Tier 2

# --- Untracked files: enumerate, then split into relevant/excluded ------------------
$untrackedAll = @(& git ls-files --others --exclude-standard)
$untrackedList = ($untrackedAll | Sort-Object)
$untrackedListing = if ($untrackedList.Count -gt 0) { ($untrackedList -join "`n") } else { "(none)" }
Add-Section -Title "Untracked files (all, names only)" -RelPath "(git ls-files --others)" `
    -Content "$fence`n$untrackedListing`n$fence" -Tier 1

# Files considered "relevant changed source" for this review: anything new
# under a source/workflow directory, or a root-level config/doc file, that
# isn't itself excluded/binary/a generated report. A file being outside
# backend/ is NEVER by itself a reason to omit it - reviewing .ai-workflow
# scripts, Docker Compose config, and root docs matters exactly as much as
# reviewing backend/ source when a task's diff touches them.
$securityCriticalHints = @(
    "core/identity.py", "core/authorization.py", "core/tenant_context.py",
    "core/config.py", "core/errors.py", "core/background_context.py", "core/audit.py"
)

# Top-level directories whose untracked files are always candidates for
# full-content review.
$includedTopLevelDirs = @("backend", "frontend", ".ai-workflow", ".vscode")
# `reports/` is excluded from this generic loop on purpose: it holds
# generated output (including this very script's own packet/prompt/raw/
# stderr/JSON/review-latest.md) which must never recursively include
# itself. The two report files that ARE genuine review evidence
# (quality-gates-latest.md, migration-validation-latest.md) get their own
# dedicated sections elsewhere in this script, not via this generic loop.
$excludedTopLevelDirs = @("reports")
# Root-level (no directory segment) files worth reviewing by extension or
# exact name.
$includedRootExtensions = @(".md", ".yml", ".yaml", ".toml", ".json")
$includedRootFilenames = @("docker-compose.yml", "docker-compose.override.yml", ".env.example", ".gitignore")

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

$relevantFiles = [System.Collections.Generic.List[string]]::new()
foreach ($rel in $untrackedList) {
    $relNorm = $rel -replace '\\', '/'

    if (-not (Test-PathInsideRepoRoot -Root $root -RelativePath $rel)) {
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

    $fullPath = Join-Path $root $rel
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
    $fullPath = Join-Path $root $relNorm
    $content = Get-Content -Raw -Encoding utf8 -Path $fullPath
    $tier = 3
    foreach ($hint in $securityCriticalHints) {
        if ($relNorm.EndsWith($hint)) { $tier = 1; break }
    }
    Add-Section -Title "Untracked file: $relNorm" -RelPath $relNorm `
        -Content "$fence$(Get-FenceLang $relNorm)`n$content`n$fence" -Tier $tier
    $reviewedCandidates.Add($relNorm)
}

# --- Tracked, changed files (modified, or added-then-committed when
# $diffRangeArg is set): the diff above is real but only shows hunks -
# embed full current content too, so the packet actually delivers what this
# prompt promises ("the full contents of new/changed files"), not just a
# diff for tracked files. Same relevance/exclusion/binary rules as
# untracked files above. ------------------------------------------------
$trackedModifiedAll = @(& git diff --name-only @diffRangeArg)
$trackedModifiedList = ($trackedModifiedAll | Sort-Object)

foreach ($rel in $trackedModifiedList) {
    $relNorm = $rel -replace '\\', '/'

    if (-not (Test-PathInsideRepoRoot -Root $root -RelativePath $rel)) {
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

    $fullPath = Join-Path $root $rel
    if (-not (Test-Path $fullPath -PathType Leaf)) {
        Add-Omitted -RelPath $relNorm -Reason "listed as modified but not readable as a file - possibly deleted"
        continue
    }
    if (Test-BinaryFile -Path $fullPath) {
        Add-Omitted -RelPath $relNorm -Reason "binary file - not embedded as text"
        continue
    }

    $content = Get-Content -Raw -Encoding utf8 -Path $fullPath
    $tier = 3
    foreach ($hint in $securityCriticalHints) {
        if ($relNorm.EndsWith($hint)) { $tier = 1; break }
    }
    Add-Section -Title "Tracked modified file (full current content): $relNorm" -RelPath $relNorm `
        -Content "$fence$(Get-FenceLang $relNorm)`n$content`n$fence" -Tier $tier
    if (-not $reviewedCandidates.Contains($relNorm)) { $reviewedCandidates.Add($relNorm) }
}

# --- Tier 1: quality-gate report / test output / migration / completion report -----
# Both paths here are in $EvidenceAllowlist - embedded via Add-EvidenceSection
# so they are never also left in the final omitted list (see the dedup pass
# below), even though the generic reports/ collection pass above may have
# already recorded them there.
$qualityGatePath = Join-Path $root "reports\quality-gates-latest.md"
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

$migrationReportPath = Join-Path $root "reports\migration-validation-latest.md"
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
# Evidence-vs-omitted dedup: a file explicitly embedded via
# Add-EvidenceSection must never also appear in the final omitted list, even
# though the generic reports/ collection pass (which runs before these
# dedicated sections) may have already recorded it there under a generic
# "not under a reviewed directory" reason. Everything else that pass
# recorded - including this tooling's own self-referential output
# (review-latest.md, codex-review-*.{md,txt,json} under the task's reports
# subfolder) - is NOT in $EvidenceAllowlist and stays correctly omitted.
# ---------------------------------------------------------------------------
$embeddedPaths = @($embeddedEvidence | ForEach-Object { $_.Path })
if ($embeddedPaths.Count -gt 0) {
    $omitted = [System.Collections.Generic.List[hashtable]]::new(
        [hashtable[]]@($omitted | Where-Object { $embeddedPaths -notcontains $_.Path })
    )
}

# ---------------------------------------------------------------------------
# Size handling: drop lowest-priority (highest Tier number) sections first,
# largest-first within a tier, until under budget. Never truncate a
# section's content - only drop it whole, and record it as omitted.
# ---------------------------------------------------------------------------
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
        Add-Omitted -RelPath $candidate.Path -Reason "dropped: packet exceeded the $MaxPacketChars-character size budget"
        $totalChars = Get-TotalChars -Secs $sections
    }
}

$finalTotalChars = Get-TotalChars -Secs $sections
if ($finalTotalChars -gt $MaxPacketChars -or $missingGoverningDocs.Count -eq $governingDocs.Count) {
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
if ($embeddedEvidence.Count -gt 0) {
    $packetLines += "## Embedded evidence files"
    $packetLines += ""
    $packetLines += "Excluded from the generic reports/ collection pass above, but explicitly"
    $packetLines += "allowlisted and embedded in full elsewhere in this packet as review"
    $packetLines += "evidence. These must NOT also appear under `"Omitted files`" below."
    $packetLines += ""
    foreach ($e in ($embeddedEvidence | Sort-Object -Property Path)) {
        $packetLines += "- $($e.Path) - $($e.Description)"
    }
    $packetLines += ""
}
if ($omitted.Count -gt 0) {
    $packetLines += "## Omitted files"
    $packetLines += ""
    foreach ($o in ($omitted | Sort-Object -Property Path)) {
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
    $embeddedPathSet = @($embeddedEvidence | ForEach-Object { $_.Path })
    $omittedPathSet = @($omitted | ForEach-Object { $_.Path })
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
    Write-Host "Reviewed-file candidates: $($reviewedCandidates.Count); omitted: $($omitted.Count)."
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
