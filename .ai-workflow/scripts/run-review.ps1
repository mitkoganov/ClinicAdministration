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
# renamed/untracked/report-noise/clean-tree/incremental-baseline scenarios,
# and asserts the packet-construction logic (the same Build-ReviewPacket
# function a real review uses - not a re-implemented approximation)
# handles every one of them correctly with no duplicate entries and no
# mutation of the disposable repo's own Git index. Exits non-zero without
# invoking Codex if any assertion fails.
#
# -Incremental (with -BaselineCommit, optionally -ToCommit) reviews only
# the commits after a chosen, already-reviewed baseline, instead of the
# full merge-base-with-master diff every -FullBranch (default) run
# re-embeds. Root cause this exists to fix: on a long-running repair
# branch, that full-branch diff keeps growing every round, and pinning
# individual files to survive the size budget just moves the omission to
# a DIFFERENT unpinned file next round (see MED-004's repair history) -
# the size budget itself was never the real problem, re-reviewing the
# entire branch from scratch every round was. Incremental mode instead:
#   - diffs from $BaselineCommit to the current tree (a single git ref,
#     not a range, so uncommitted staged/unstaged work on top of HEAD is
#     still included - exactly how -FullBranch's "uncommitted" mode
#     already diffs against a single ref, just pointed at the baseline
#     instead of HEAD);
#   - marks EVERY file touched by that diff as Tier 1 (never droppable),
#     not just a hand-maintained hint list - the omission this whole
#     mode exists to prevent can no longer recur for anything actually
#     reviewed;
#   - adds a fixed, documented auth-context allowlist (session/CSRF/auth
#     service/user-account files - see $authContextAllowlist below) as
#     Tier 2 (droppable only if the packet cannot otherwise fit, never
#     silently, since Incremental's drop threshold only removes Tier 3);
#   - adds compact, Git-derived baseline evidence (commit list, blob
#     hashes, prior repair commit messages) instead of re-embedding full
#     file contents for anything already covered by the baseline;
#   - hard-fails packet generation (never produces an incomplete packet)
#     if the baseline is not an ancestor of the target commit, if the
#     target commit does not match actual HEAD, or if any file the
#     incremental diff touched is missing a Tier 0/1 section afterward.
# -ExcludeContextManifest (default off, i.e. the manifest is included by
# default) drops the compact Tier 2 baseline-evidence/frontend-unchanged-
# evidence sections, keeping Tier 0/1 only, for a smaller diagnostic
# packet.
#
# -IncludeVerboseEvidence (default off): the two allowlisted evidence
# reports (reports/quality-gates-latest.md, reports/migration-validation-
# latest.md) are embedded as a COMPACT, deterministically-extracted
# summary by default (see Get-CompactEvidence below) - full verbatim
# embedding (the old, default-on behavior) is what grew an Incremental
# packet past budget on a long-running branch: these reports accumulate
# verbose Docker build logs, package-download output, and repeated
# command transcripts round after round, none of which a reviewer needs
# to verify "did the gates pass" - only the PASS/FAIL per gate and the
# test counts do. Pass this switch to fall back to full verbatim
# embedding for debugging the extraction itself. Compact evidence is
# still Tier 1 (never dropped) and still goes through the same
# $EvidenceAllowlist/Add-EvidenceSection path as before - only the
# CONTENT embedded for those two paths changed, not their tier or
# allowlist status.
#
# -ValidateCompactEvidence: builds several compact-evidence extractions
# against disposable temporary report files (never this repo's own
# reports/) covering a passing report, a report with a failed gate, a
# report missing its machine-readable summary block, and a migration
# report with a failed step - asserts Get-CompactEvidence extracts the
# right fields, hard-fails (throws) on a missing marker or a non-PASS
# result, never embeds the raw verbose body, and produces a summary far
# smaller than the raw input. Exits non-zero without invoking Codex if
# any assertion fails.

param(
    [switch]$DryRun, [switch]$ValidateProvenance, [switch]$ValidateDiffCollection,
    [switch]$Incremental, [string]$BaselineCommit, [string]$ToCommit,
    [switch]$ExcludeContextManifest, [switch]$IncludeVerboseEvidence,
    [switch]$ValidateCompactEvidence
)

. "$PSScriptRoot\common.ps1"

$root = Get-RepoRoot
$fence = [string]::new('`', 3)

# ===========================================================================
# Compact evidence extraction (see -IncludeVerboseEvidence above for why).
# Deterministic: every field comes from parsing the raw report's own
# "## Machine-readable summary" fenced key:value block (written by
# run-tests.ps1 for reports/quality-gates-latest.md, and hand-maintained
# for reports/migration-validation-latest.md, which has no separate
# generator script) plus a SHA-256 hash of the complete raw file - never a
# freely-invented summary. Throws (hard fail, never a partially-built
# packet) if the marker block is missing, if a required key is absent, or
# if the extracted overall/step result is not PASS - a compact summary
# must never be able to claim PASS when the raw report does not actually
# show it.
# ===========================================================================
function Get-Sha256Hex {
    param([string]$Content)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Content)
        $hash = $sha256.ComputeHash($bytes)
        return -join ($hash | ForEach-Object { $_.ToString("x2") })
    } finally {
        $sha256.Dispose()
    }
}

function Get-MachineReadableSummaryBlock {
    # Extracts the key:value lines inside the FIRST fenced block that
    # immediately follows a "## Machine-readable summary" heading. Returns
    # $null (never a partial/best-guess hashtable) if the heading or its
    # fence is not found, so callers can hard-fail deterministically.
    param([string]$RawContent)
    $match = [regex]::Match(
        $RawContent,
        '##\s*Machine-readable summary\s*\r?\n\r?\n```[^\r\n]*\r?\n(?<body>.*?)\r?\n```',
        [System.Text.RegularExpressions.RegexOptions]::Singleline
    )
    if (-not $match.Success) { return $null }
    $fields = @{}
    foreach ($line in ($match.Groups["body"].Value -split "`r?`n")) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        $parts = $line -split ":", 2
        if ($parts.Count -eq 2) { $fields[$parts[0].Trim()] = $parts[1].Trim() }
    }
    return $fields
}

function Get-CompactEvidence {
    # $RequiredKeys: keys that MUST be present (hard fail otherwise).
    # $PassKeys: keys whose value must literally be "PASS" (hard fail on
    # any other value, including "FAIL" or "unknown") for this evidence to
    # be usable at all - the whole point is a compact summary can never
    # assert something the raw report doesn't actually show.
    param(
        [Parameter(Mandatory)][string]$RawContent,
        [Parameter(Mandatory)][string]$RelPath,
        [string[]]$RequiredKeys,
        [string[]]$PassKeys
    )
    $hash = Get-Sha256Hex -Content $RawContent
    $rawChars = $RawContent.Length
    $fields = Get-MachineReadableSummaryBlock -RawContent $RawContent
    if ($null -eq $fields) {
        throw "Compact evidence extraction FAILED for '$RelPath': no '## Machine-readable summary' fenced block found. Regenerate this report (it must include that section) before running the review."
    }
    foreach ($key in $RequiredKeys) {
        if (-not $fields.ContainsKey($key)) {
            throw "Compact evidence extraction FAILED for '$RelPath': required key '$key' is missing from its machine-readable summary block."
        }
    }
    foreach ($key in $PassKeys) {
        if ($fields[$key] -ne "PASS") {
            throw "Compact evidence extraction FAILED for '$RelPath': '$key' is '$($fields[$key])', not PASS. A failed/incomplete evidence report can never be presented to Codex as passing - fix the underlying failure and regenerate the report first."
        }
    }
    $summaryLines = [System.Collections.Generic.List[string]]::new()
    $summaryLines.Add("Evidence mode: compact (deterministically extracted from the raw report's own machine-readable summary block - see -IncludeVerboseEvidence to embed the full raw report instead).")
    $summaryLines.Add("Raw report path: $RelPath")
    $summaryLines.Add("Raw report SHA-256: $hash")
    $summaryLines.Add("Raw report size: $rawChars characters")
    foreach ($key in ($fields.Keys | Sort-Object)) {
        $summaryLines.Add("${key}: $($fields[$key])")
    }
    $compactContent = $summaryLines -join "`n"
    return @{
        Hash = $hash
        RawChars = $rawChars
        CompactChars = $compactContent.Length
        Fields = $fields
        CompactContent = $compactContent
    }
}

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
    "tests/integration/test_invitation_service.py",
    "tests/integration/test_auth_service.py",
    "tests/conftest.py"
)

# Incremental mode's fixed auth context profile (see this script's header
# comment on -Incremental): the minimum set of files Codex needs to
# understand ANY auth/session/CSRF/password repair, regardless of whether
# the current incremental diff happens to touch them. Embedded as Tier 2
# context (see $authContextAllowlist below) ONLY when not already covered
# by the current diff's own Tier 1 sections - this is deliberately a flat,
# explicit list, not a dependency-graph walk: this task's own instructions
# call for "minimally надеждния auth context profile", not a general
# dependency parser. Extend this list (or add a sibling profile, e.g. a
# tenancy or appointments context) if a future incremental review needs a
# different fixed context set.
$authContextAllowlist = @(
    "backend/app/core/session_dependency.py",
    "backend/app/services/session_service.py",
    "backend/app/core/csrf.py",
    "backend/app/core/errors.py",
    "backend/app/core/session_cookies.py",
    "backend/app/core/tenant_context.py",
    "backend/app/services/auth_service.py",
    "backend/app/repositories/user_account.py",
    "backend/app/models/user_account.py",
    "backend/app/core/passwords.py",
    "backend/app/api/auth.py",
    "backend/tests/integration/auth_api_helpers.py",
    "backend/tests/integration/conftest.py",
    "backend/tests/conftest.py"
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
        [string[]]$ProtectedBranches = @(),
        [switch]$Incremental,
        [string]$BaselineCommit,
        [string]$ToCommit,
        [switch]$ExcludeContextManifest,
        [switch]$IncludeVerboseEvidence
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
    $baselineResolved = $null
    $toCommitResolved = $null

    # --- Incremental mode: diff from an explicit, already-reviewed
    # baseline commit to the current tree, instead of the full
    # merge-base-with-master diff -FullBranch (default) always re-embeds.
    # Uses a SINGLE git ref (not a range) for $diffRangeArg - exactly like
    # the default "uncommitted" mode above uses a bare "HEAD" - so `git
    # diff <ref>` naturally combines every commit since the baseline WITH
    # any currently staged/unstaged working-tree changes into one diff,
    # satisfying "uncommitted changes are still included" without a
    # second collection pass. Every validation failure below is a hard
    # `throw`, never a partially-built packet - an incremental review
    # with a wrong/unverifiable baseline is worse than no review at all,
    # since it would silently under-review the branch.
    if ($Incremental) {
        if ([string]::IsNullOrWhiteSpace($BaselineCommit)) {
            throw "Incremental mode requires -BaselineCommit (a commit hash/ref that was already reviewed to completion)."
        }
        & git -C $RepoRoot rev-parse --verify --quiet "$BaselineCommit^{commit}" *>$null
        if ($LASTEXITCODE -ne 0) {
            throw "Incremental mode: -BaselineCommit '$BaselineCommit' does not resolve to a valid commit in this repository."
        }
        $baselineResolved = ((& git -C $RepoRoot rev-parse $BaselineCommit) | Out-String).Trim()
        $actualHead = ((& git -C $RepoRoot rev-parse HEAD) | Out-String).Trim()
        if ([string]::IsNullOrWhiteSpace($ToCommit)) {
            $toCommitResolved = $actualHead
        } else {
            & git -C $RepoRoot rev-parse --verify --quiet "$ToCommit^{commit}" *>$null
            if ($LASTEXITCODE -ne 0) {
                throw "Incremental mode: -ToCommit '$ToCommit' does not resolve to a valid commit in this repository."
            }
            $toCommitResolved = ((& git -C $RepoRoot rev-parse $ToCommit) | Out-String).Trim()
            if ($toCommitResolved -ne $actualHead) {
                throw "Incremental mode: -ToCommit '$ToCommit' (resolves to $toCommitResolved) does not match the actual current HEAD ($actualHead). Refusing to review any target other than the real current HEAD - commit whatever this review should cover first."
            }
        }
        & git -C $RepoRoot merge-base --is-ancestor $baselineResolved $toCommitResolved
        if ($LASTEXITCODE -ne 0) {
            throw "Incremental mode: baseline commit '$baselineResolved' is not an ancestor of target commit '$toCommitResolved'. A baseline must be an already-reviewed point strictly behind the review target."
        }
        $mode = "incremental"
        $diffRangeArg = @($baselineResolved)
    } elseif ([string]::IsNullOrWhiteSpace($statusShortExcludingReports)) {
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

    # --- Tier 1: review-mode header - states in the packet itself (not just
    # this script's own comments) what kind of review this is, so Codex
    # never has to guess whether "unchanged" claims are trustworthy. ------
    if ($mode -eq "incremental") {
        $headerLines = @(
            "This is an INCREMENTAL review, not a full-branch review.",
            "",
            "- Baseline commit (already reviewed to completion): $baselineResolved",
            "- Target commit (current HEAD): $toCommitResolved",
            "- Diff range collected: everything reachable from '$baselineResolved' to the current working tree (commits + any staged/unstaged changes on top)",
            "- Tier legend for this packet:",
            "  - Tier 1: governing docs, this header, git status/diff, every file touched by the incremental diff, quality/migration evidence, the changed-file completeness manifest - never dropped",
            "  - Tier 2: fixed auth context files not already covered by the diff, compact Git-derived baseline evidence, frontend-unchanged evidence - dropped only if the packet cannot otherwise fit under budget",
            "  - Tier 3: anything else - dropped first under budget pressure",
            "- Do not request the full contents of files outside this packet on the theory that 'unchanged' cannot be trusted without them: the baseline evidence section below is Git-derived (commit hashes, blob hashes) precisely so unchanged state is verifiable without re-embedding it.",
            "- The quality-gate and migration evidence sections below are COMPACT summaries, not the full raw report text: each is deterministically extracted from that report's own 'Machine-readable summary' block, plus a SHA-256 hash and character count of the complete raw file on disk. Extraction hard-fails before this review would even run if a required marker is missing or if any extracted result is not PASS - so a compact summary present in this packet is exactly as trustworthy as the full raw log would be. This is an intentional evidence-compaction choice (see run-review.ps1's -IncludeVerboseEvidence), never treat the absence of the raw Docker/Alembic log text as an omission worth a REVIEW_INCOMPLETE.",
            "- Only report REVIEW_INCOMPLETE if a specific file this incremental diff actually touched, or a specific fixed auth-context file, is missing from this packet - not for optional/unrelated repository content, and not for the raw (non-compact) form of the quality/migration evidence."
        ) -join "`n"
        Add-Section -Title "Review mode: incremental" -RelPath "(review mode header)" -Content $headerLines -Tier 1
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

    # Paths already embedded via a dedicated section above (currently: only
    # governing docs) - the generic untracked/renamed/tracked-changed-file
    # loops below must skip these entirely rather than embedding them a
    # SECOND time. A governing doc like ARCHITECTURE.md or SECURITY.md is a
    # root-level .md file, which Test-ReviewRelevantPath treats as generically
    # reviewable - so whenever one of them also appears in `git diff
    # --name-status` (normal on a long-running branch), the generic loop
    # would otherwise add a second "Tracked changed file" section with the
    # exact same content, wasting packet budget and confusing the reviewer
    # with duplicate context for the same file.
    $alreadyEmbeddedNormalizedPaths = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($c in $reviewedCandidates) { [void]$alreadyEmbeddedNormalizedPaths.Add((ConvertTo-NormalizedReviewPath $c)) }

    # --- Tier 1: git status / diff stat ------------------------------------------------
    Add-Section -Title "git status --short" -RelPath "(git status --short)" `
        -Content "$fence`n$statusShort`n$fence" -Tier 1

    $diffStat = ((& git -C $RepoRoot diff --stat -M @diffRangeArg) | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($diffStat)) { $diffStat = "(no diff in range - all changes are untracked new files; see below)" }
    Add-Section -Title "git diff --stat -M $diffRangeArg" -RelPath "(git diff --stat)" `
        -Content "$fence`n$diffStat`n$fence" -Tier 1

    # --- Tier 1: full unified diff, relative to the SAME baseline used for
    # the file list below - staged and unstaged tracked changes combined,
    # never a bare working-tree-vs-index diff. Tier 1 (not 2) unconditionally:
    # the whole diff is exactly the evidence a review cannot meaningfully
    # proceed without, in every mode. ------------------------------------------------
    $baselineDescription = switch ($mode) {
        "uncommitted"          { "HEAD (staged + unstaged combined)" }
        "incremental"          { "the review baseline commit $baselineResolved (staged + unstaged combined)" }
        default                 { "the merge-base commit" }
    }
    # Wholly-added tracked files (git status "A") are excluded from THIS
    # diff via pathspec and diffed separately below with --unified=0 (a
    # bare "every line is new" hunk, no surrounding context) - the
    # per-file "Tracked changed file (full current content)" section
    # created later in this function already embeds that same file's
    # complete final content in full, so a --unified=80 rendering of a
    # pure addition here would be near-total duplication (every line
    # appears twice in the packet for zero additional review value,
    # since there is no "before" state to contrast against). Modified/
    # renamed/deleted files keep their full --unified=80 context here,
    # where the delta itself (not just the final state) is the evidence
    # that matters. This does not weaken Tier 1/completeness: added files
    # remain Tier 1 via their own per-file section either way (see the
    # changed-file completeness validation below, which checks for a
    # per-PATH section, not this combined diff section).
    $addedFilePaths = [System.Collections.Generic.List[string]]::new()
    foreach ($line in @(& git -C $RepoRoot diff --name-status -M @diffRangeArg)) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        $fields = $line -split "`t"
        if ($fields[0] -eq "A") { $addedFilePaths.Add($fields[1]) }
    }
    $diffPathspecExclusions = $addedFilePaths | ForEach-Object { ":(exclude)$_" }
    $fullDiffLines = & git -C $RepoRoot diff --no-ext-diff -M --unified=80 @diffRangeArg -- . @diffPathspecExclusions
    $fullDiff = ($fullDiffLines | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($fullDiff)) { $fullDiff = "(empty relative to $baselineDescription, after excluding wholly-added files below - either no tracked-file changes at all, or every tracked change in range is itself a wholly-added file.)" }
    $addedFilesNote = if ($addedFilePaths.Count -gt 0) {
        "`n`nWholly-added files ($($addedFilePaths.Count)) are intentionally NOT re-diffed here (this would just repeat every line as a `"+`" addition with zero prior state to contrast against) - each has its own full-content `"Tracked changed file`"/`"Untracked file`" section below instead, which is the complete review evidence for a pure addition:`n" + ($addedFilePaths -join "`n")
    } else { "" }
    Add-Section -Title "git diff --no-ext-diff -M --unified=80 $diffRangeArg (tracked MODIFIED/renamed/deleted file changes, relative to $baselineDescription - wholly-added files are excluded here, see note)" -RelPath "(git diff)" `
        -Content "$fence`ndiff`n$fullDiff`n$fence$addedFilesNote" -Tier 1

    # In Incremental mode, every file the diff touches is Tier 1 (never
    # droppable) - the whole point of this mode is that nothing the
    # current repair actually changed can be silently omitted. In
    # FullBranch mode, the default stays Tier 3 (droppable), same as
    # before this mode existed - only $securityCriticalHints/
    # $authTestCoverageHints-matched paths are pinned to Tier 1 there.
    $defaultChangedFileTier = if ($mode -eq "incremental") { 1 } else { 3 }

    # --- Untracked files: enumerate, then split into relevant/excluded ------------------
    $untrackedAll = @(& git -C $RepoRoot ls-files --others --exclude-standard)
    $untrackedList = ($untrackedAll | Sort-Object)
    $untrackedListing = if ($untrackedList.Count -gt 0) { ($untrackedList -join "`n") } else { "(none)" }
    Add-Section -Title "Untracked files (all, names only)" -RelPath "(git ls-files --others)" `
        -Content "$fence`n$untrackedListing`n$fence" -Tier 1

    $relevantFiles = [System.Collections.Generic.List[string]]::new()
    foreach ($rel in $untrackedList) {
        $relNorm = $rel -replace '\\', '/'

        if ($alreadyEmbeddedNormalizedPaths.Contains((ConvertTo-NormalizedReviewPath $relNorm))) {
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
        $tier = $defaultChangedFileTier
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

            if ($alreadyEmbeddedNormalizedPaths.Contains((ConvertTo-NormalizedReviewPath $newRel))) {
                continue
            }
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
            $tier = $defaultChangedFileTier
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

        if ($alreadyEmbeddedNormalizedPaths.Contains((ConvertTo-NormalizedReviewPath $relNorm))) {
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
        $tier = $defaultChangedFileTier
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
        if ($IncludeVerboseEvidence) {
            $qgSectionContent = "$fence`n$qgContent`n$fence"
        } else {
            $qgEvidence = Get-CompactEvidence -RawContent $qgContent -RelPath "reports/quality-gates-latest.md" `
                -RequiredKeys @("overall_result", "backend_unit_test_count", "backend_integration_test_count", "backend_total_test_count") `
                -PassKeys @("overall_result")
            $qgSectionContent = "$fence`n$($qgEvidence.CompactContent)`n$fence"
        }
        Add-EvidenceSection -RelPath "reports/quality-gates-latest.md" `
            -Title "Latest quality-gate report (reports/quality-gates-latest.md)" `
            -Content $qgSectionContent -Tier 1 `
            -Description "Latest official quality-gate run output (ruff/mypy/pytest/frontend/Docker checks) - $(if ($IncludeVerboseEvidence) { 'full raw report' } else { 'compact, hash-verified summary (see -IncludeVerboseEvidence for the full raw report)' })."
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
        if ($IncludeVerboseEvidence) {
            $migSectionContent = "$fence`n$migrationContent`n$fence"
        } else {
            $migEvidence = Get-CompactEvidence -RawContent $migrationContent -RelPath "reports/migration-validation-latest.md" `
                -RequiredKeys @("target_revision", "upgrade_result", "downgrade_result", "reupgrade_result", "provider_exclusion_constraint", "room_exclusion_constraint") `
                -PassKeys @("upgrade_result", "downgrade_result", "reupgrade_result", "provider_exclusion_constraint", "room_exclusion_constraint")
            $migSectionContent = "$fence`n$($migEvidence.CompactContent)`n$fence"
        }
        Add-EvidenceSection -RelPath "reports/migration-validation-latest.md" `
            -Title "Migration upgrade/downgrade output (reports/migration-validation-latest.md)" `
            -Content $migSectionContent -Tier 1 `
            -Description "Alembic upgrade/downgrade/re-upgrade validation transcript - $(if ($IncludeVerboseEvidence) { 'full raw report' } else { 'compact, hash-verified summary (see -IncludeVerboseEvidence for the full raw report)' })."
    } else {
        Add-Section -Title "Migration upgrade/downgrade output" -RelPath "(migration validation output)" `
            -Content "NOT AVAILABLE - reports/migration-validation-latest.md does not exist." -Tier 1
        Add-Omitted -RelPath "reports/migration-validation-latest.md" -Reason "not found"
    }

    Add-Section -Title "Claude completion report" -RelPath "(completion report)" `
        -Content "NOT AVAILABLE - the implementation completion report was communicated in the chat session and was not persisted to a file in this repository." -Tier 1

    if ($mode -eq "incremental") {
        # --- Tier 2: fixed auth context files not already covered by the
        # current diff - see $authContextAllowlist's header comment. -----
        $liveEmbeddedNormalized = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        foreach ($c in $reviewedCandidates) { [void]$liveEmbeddedNormalized.Add((ConvertTo-NormalizedReviewPath $c)) }
        foreach ($contextPath in $authContextAllowlist) {
            if ($liveEmbeddedNormalized.Contains((ConvertTo-NormalizedReviewPath $contextPath))) { continue }
            $fullContextPath = Join-Path $RepoRoot $contextPath
            if (-not (Test-Path $fullContextPath -PathType Leaf)) {
                Add-Omitted -RelPath $contextPath -Reason "auth context file not found on disk - may have been renamed/removed"
                continue
            }
            $contextContent = Get-Content -Raw -Encoding utf8 -Path $fullContextPath
            Add-Section -Title "Context file (unchanged in this incremental range): $contextPath" -RelPath $contextPath `
                -Content "$fence$(Get-FenceLang $contextPath)`n$contextContent`n$fence" -Tier 2
            $reviewedCandidates.Add($contextPath)
            [void]$liveEmbeddedNormalized.Add((ConvertTo-NormalizedReviewPath $contextPath))
        }

        if (-not $ExcludeContextManifest) {
            # --- Tier 2: compact, Git-derived baseline evidence - commit
            # list and blob hashes prove what changed (and what didn't)
            # since the baseline WITHOUT re-embedding full file contents
            # for anything the baseline itself already covers. -----------
            $commitListRaw = ((& git -C $RepoRoot log --oneline "$baselineResolved..$toCommitResolved") | Out-String).Trim()
            if ([string]::IsNullOrWhiteSpace($commitListRaw)) { $commitListRaw = "(no commits - baseline and target are the same commit)" }

            $baselineSubject = ((& git -C $RepoRoot log -1 --format=%s $baselineResolved) | Out-String).Trim()
            $headSubject = ((& git -C $RepoRoot log -1 --format=%s $toCommitResolved) | Out-String).Trim()

            $blobHashLines = [System.Collections.Generic.List[string]]::new()
            foreach ($contextPath in ($authContextAllowlist | Sort-Object)) {
                $atBaseline = ((& git -C $RepoRoot rev-parse --verify --quiet "${baselineResolved}:${contextPath}") | Out-String).Trim()
                if ([string]::IsNullOrWhiteSpace($atBaseline)) { $atBaseline = "(did not exist at baseline)" }
                $atHead = ((& git -C $RepoRoot rev-parse --verify --quiet "${toCommitResolved}:${contextPath}") | Out-String).Trim()
                if ([string]::IsNullOrWhiteSpace($atHead)) { $atHead = "(does not exist at HEAD)" }
                $changedMarker = if ($atBaseline -eq $atHead) { "unchanged" } else { "CHANGED" }
                $blobHashLines.Add("$contextPath : baseline=$atBaseline head=$atHead ($changedMarker)")
            }

            $baselineEvidenceLines = @(
                "Repository root: $RepoRoot",
                "Baseline commit: $baselineResolved ($baselineSubject)",
                "Target/HEAD commit: $toCommitResolved ($headSubject)",
                "",
                "Commits in incremental scope (baseline..HEAD, oldest first shown by git log --oneline):",
                $commitListRaw,
                "",
                "Fixed auth-context file blob hashes (proves, via Git object hashes rather than prose, exactly which context files this incremental range did or did not touch):",
                ($blobHashLines -join "`n")
            ) -join "`n"
            Add-Section -Title "Baseline evidence (Git-derived)" -RelPath "(baseline evidence)" `
                -Content $baselineEvidenceLines -Tier 2

            # --- Tier 2: prior repair history - Git-derived (commit
            # messages), not free text - explains WHY the baseline itself
            # already contains what it contains, without re-embedding any
            # of that code. Commit messages in this repository already
            # document root cause + fix for each round (see `git log`
            # above this baseline), so this is literally that log, not a
            # hand-written summary. -----------------------------------
            $repairHistoryBaseRef = $null
            foreach ($candidate in $ProtectedBranches) {
                & git -C $RepoRoot rev-parse --verify --quiet $candidate *>$null
                if ($LASTEXITCODE -eq 0) { $repairHistoryBaseRef = $candidate; break }
            }
            if ($repairHistoryBaseRef) {
                $repairHistoryBase = ((& git -C $RepoRoot merge-base $baselineResolved $repairHistoryBaseRef) | Out-String).Trim()
                $repairHistoryRaw = ((& git -C $RepoRoot log --format="commit %H%n%B" "$repairHistoryBase..$baselineResolved") | Out-String).Trim()
                if ([string]::IsNullOrWhiteSpace($repairHistoryRaw)) { $repairHistoryRaw = "(baseline commit has no ancestry beyond $repairHistoryBaseRef - nothing to summarize)" }
                Add-Section -Title "Repair history already covered by the baseline (commit messages, $repairHistoryBase..$baselineResolved)" `
                    -RelPath "(repair history)" -Content "$fence`n$repairHistoryRaw`n$fence" -Tier 2
            }

            # --- Tier 2: frontend-unchanged evidence - a compact manifest
            # with blob hashes, never full re-embedded content, unless the
            # incremental diff itself touched a frontend file (in which
            # case it is already Tier 1 via the normal diff-collection
            # loop above). ------------------------------------------------
            $frontendChangedInRange = ((& git -C $RepoRoot diff --name-only -M $baselineResolved -- frontend) | Out-String).Trim()
            $frontendTrackedFiles = @(& git -C $RepoRoot ls-files -- frontend) | Sort-Object
            $frontendManifestLines = [System.Collections.Generic.List[string]]::new()
            foreach ($fp in $frontendTrackedFiles) {
                $fpHash = ((& git -C $RepoRoot rev-parse --verify --quiet "${toCommitResolved}:${fp}") | Out-String).Trim()
                if ([string]::IsNullOrWhiteSpace($fpHash)) { $fpHash = "(not present at HEAD)" }
                $frontendManifestLines.Add("$fp : $fpHash")
            }
            $frontendChangeStatusLine = if ([string]::IsNullOrWhiteSpace($frontendChangedInRange)) {
                "No frontend files are touched by this incremental diff (baseline..HEAD, plus any staged/unstaged changes)."
            } else {
                "WARNING: the incremental diff DOES touch frontend files - they are embedded above via the normal diff-collection loop, not summarized here:`n$frontendChangedInRange"
            }
            $frontendEvidenceLines = @(
                $frontendChangeStatusLine,
                "",
                "Frontend quality evidence (lint/typecheck/build) is embedded in full via the quality-gate report section above - not duplicated here.",
                "",
                "Tracked frontend file manifest at HEAD ($($frontendTrackedFiles.Count) files, path : blob hash):",
                ($frontendManifestLines -join "`n")
            ) -join "`n"
            Add-Section -Title "Frontend unchanged-state evidence" -RelPath "(frontend evidence)" `
                -Content $frontendEvidenceLines -Tier 2
        }

        # ---------------------------------------------------------------
        # Changed-file completeness validation: every path `git diff
        # --name-status` reports for $diffRangeArg must correspond to a
        # Tier <= 2 section (added/modified/renamed-new-path), a deleted-
        # file entry, or a renamed-old-path entry. This is deliberately a
        # hard `throw` (never a partially-built, silently-incomplete
        # packet) - an incremental review that cannot prove it saw every
        # changed file is worse than no review, since a wrong "nothing
        # missing" packet would look identical to a genuinely complete one.
        # ---------------------------------------------------------------
        $completenessNameStatus = @(& git -C $RepoRoot diff --name-status -M @diffRangeArg)
        $embeddedForCompleteness = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        foreach ($s in $sections) {
            if ($s.Tier -le 2 -and $s.Path) { [void]$embeddedForCompleteness.Add((ConvertTo-NormalizedReviewPath $s.Path)) }
        }
        foreach ($d in $deletedFiles) { [void]$embeddedForCompleteness.Add((ConvertTo-NormalizedReviewPath $d)) }
        foreach ($r in $renamedFiles) {
            [void]$embeddedForCompleteness.Add((ConvertTo-NormalizedReviewPath $r.OldPath))
            [void]$embeddedForCompleteness.Add((ConvertTo-NormalizedReviewPath $r.NewPath))
        }
        $missingFromManifest = [System.Collections.Generic.List[string]]::new()
        foreach ($line in $completenessNameStatus) {
            if ([string]::IsNullOrWhiteSpace($line)) { continue }
            $fields = $line -split "`t"
            $statusCode = $fields[0]
            $checkPath = if ($statusCode.StartsWith("R") -or $statusCode.StartsWith("C")) { $fields[2] } else { $fields[1] }
            $checkPathNorm = ConvertTo-NormalizedReviewPath ($checkPath -replace '\\', '/')
            if (Test-ExcludedRelativePath -RelativePath $checkPath) { continue }
            if (-not (Test-ReviewRelevantPath -RelNorm $checkPathNorm)) { continue }
            if (-not $embeddedForCompleteness.Contains($checkPathNorm)) {
                $missingFromManifest.Add("$statusCode`t$checkPath")
            }
        }
        if ($missingFromManifest.Count -gt 0) {
            throw "Incremental review packet is INCOMPLETE: the following file(s) changed in $baselineResolved..$toCommitResolved (plus working tree) but have no Tier<=2 section, deletion marker, or rename entry: $($missingFromManifest -join '; '). Packet generation refuses to produce a silently-incomplete review - fix Build-ReviewPacket's collection logic or this file's classification before retrying."
        }
    }

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

        # 15: Incremental happy path - a baseline commit, one commit after
        # it that changes a file, reviewed with -Incremental.
        $incrementalBaseline = ((& git -C $tempRepo rev-parse HEAD) | Out-String).Trim()
        Set-Content -Path (Join-Path $tempRepo "incr-changed.md") -Value "incremental change`n" -NoNewline
        & git -C $tempRepo add "incr-changed.md" *>$null
        & git -C $tempRepo commit -q -m "incremental repair commit" *>$null
        $incrementalHead = ((& git -C $tempRepo rev-parse HEAD) | Out-String).Trim()

        $incrementalPacket = Build-ReviewPacket -RepoRoot $tempRepo -Incremental -BaselineCommit $incrementalBaseline
        if ($incrementalPacket.Mode -ne "incremental") {
            $failures.Add("Scenario 15: -Incremental did not select incremental mode (got '$($incrementalPacket.Mode)').")
        }
        $incrChangedSection = $incrementalPacket.Sections | Where-Object { $_.Path -eq "incr-changed.md" } | Select-Object -First 1
        if (-not $incrChangedSection) {
            $failures.Add("Scenario 15: incremental review did not embed the file changed since baseline (incr-changed.md).")
        } elseif ($incrChangedSection.Tier -ne 1) {
            $failures.Add("Scenario 15: incremental review embedded the changed file at Tier $($incrChangedSection.Tier), expected Tier 1 (never droppable).")
        }
        $headerSection = $incrementalPacket.Sections | Where-Object { $_.Path -eq "(review mode header)" } | Select-Object -First 1
        if (-not $headerSection -or $headerSection.Content -notmatch [regex]::Escape($incrementalBaseline)) {
            $failures.Add("Scenario 15: incremental review-mode header does not state the baseline commit.")
        }

        # 16: baseline not an ancestor of the target - a sibling branch's
        # commit used as baseline against main's HEAD must be refused.
        & git -C $tempRepo checkout -q -b sibling-branch $incrementalBaseline *>$null
        Set-Content -Path (Join-Path $tempRepo "sibling-only.md") -Value "sibling`n" -NoNewline
        & git -C $tempRepo add "sibling-only.md" *>$null
        & git -C $tempRepo commit -q -m "sibling commit" *>$null
        $siblingCommit = ((& git -C $tempRepo rev-parse HEAD) | Out-String).Trim()
        & git -C $tempRepo checkout -q main *>$null
        & git -C $tempRepo branch -q -D sibling-branch *>$null

        $notAncestorThrew = $false
        try {
            Build-ReviewPacket -RepoRoot $tempRepo -Incremental -BaselineCommit $siblingCommit -ToCommit $incrementalHead | Out-Null
        } catch {
            $notAncestorThrew = $true
        }
        if (-not $notAncestorThrew) {
            $failures.Add("Scenario 16: -Incremental did not reject a baseline commit that is not an ancestor of the target.")
        }

        # 17: invalid/unresolvable baseline commit hash must be refused.
        $invalidHashThrew = $false
        try {
            Build-ReviewPacket -RepoRoot $tempRepo -Incremental -BaselineCommit "0000000000000000000000000000000000000000" | Out-Null
        } catch {
            $invalidHashThrew = $true
        }
        if (-not $invalidHashThrew) {
            $failures.Add("Scenario 17: -Incremental did not reject an unresolvable -BaselineCommit hash.")
        }

        # 18: an explicit -ToCommit that does not match actual current
        # HEAD must be refused, never silently reviewed against the wrong
        # target.
        $headMismatchThrew = $false
        try {
            Build-ReviewPacket -RepoRoot $tempRepo -Incremental -BaselineCommit $incrementalBaseline -ToCommit $incrementalBaseline | Out-Null
        } catch {
            $headMismatchThrew = $true
        }
        if (-not $headMismatchThrew) {
            $failures.Add("Scenario 18: -Incremental did not reject a -ToCommit that does not match actual current HEAD.")
        }
    } finally {
        Remove-Item -Recurse -Force $tempRepo -ErrorAction SilentlyContinue
    }

    if ($failures.Count -gt 0) {
        Write-Host "Diff-collection validation FAILED:"
        foreach ($f in $failures) { Write-Host "  - $f" }
        exit 1
    }

    Write-Host "Diff-collection validation PASSED (18 scenarios, disposable repo at $tempRepo, now removed)."
    exit 0
}

# ===========================================================================
# -ValidateCompactEvidence: exercises Get-CompactEvidence against disposable
# temporary report files under the OS temp directory - never this repo's own
# reports/. Never invokes Codex.
# ===========================================================================
if ($ValidateCompactEvidence) {
    $failures = [System.Collections.Generic.List[string]]::new()
    $tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("review-compact-evidence-" + [System.Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tempDir | Out-Null

    try {
        $passingReport = @(
            "# Quality gate run", "",
            "## Machine-readable summary", "",
            '```',
            "overall_result: PASS",
            "gate_count: 3",
            "failed_gate_count: 0",
            "backend_unit_test_count: 184",
            "backend_integration_test_count: 367",
            "backend_total_test_count: 551",
            "gate.backend-ruff: PASS",
            '```', "",
            "## backend-ruff - PASS (exit 0)", "",
            '```', ("x" * 5000), '```'
        ) -join "`n"

        # 1: a PASS report extracts every required field correctly.
        $evidence = Get-CompactEvidence -RawContent $passingReport -RelPath "test/passing.md" `
            -RequiredKeys @("overall_result", "backend_unit_test_count", "backend_integration_test_count", "backend_total_test_count") `
            -PassKeys @("overall_result")
        if ($evidence.Fields["overall_result"] -ne "PASS") { $failures.Add("Scenario 1: overall_result was not extracted as PASS.") }
        if ($evidence.Fields["backend_unit_test_count"] -ne "184") { $failures.Add("Scenario 1: backend_unit_test_count was not extracted as 184.") }
        if ($evidence.Fields["backend_integration_test_count"] -ne "367") { $failures.Add("Scenario 1: backend_integration_test_count was not extracted as 367.") }
        if ($evidence.Fields["backend_total_test_count"] -ne "551") { $failures.Add("Scenario 1: backend_total_test_count was not extracted as 551.") }

        # 2: the SHA-256 hash is included and matches a fresh computation.
        $expectedHash = Get-Sha256Hex -Content $passingReport
        if ($evidence.Hash -ne $expectedHash) { $failures.Add("Scenario 2: extracted hash does not match a fresh SHA-256 computation of the same content.") }

        # 3: the compact summary is far smaller than the raw report, and
        # never contains the raw report's verbose body text.
        if ($evidence.CompactChars -ge $evidence.RawChars) { $failures.Add("Scenario 3: compact summary is not smaller than the raw report.") }
        if ($evidence.CompactContent -match "x{100,}") { $failures.Add("Scenario 3 (raw log leakage): compact summary embeds the raw report's verbose body text.") }

        # 4: a failed gate (overall_result: FAIL) hard-fails extraction -
        # a compact summary must never be able to claim usable evidence
        # from a report that itself shows a failure.
        $failingReport = $passingReport -replace "overall_result: PASS", "overall_result: FAIL"
        $threwOnFail = $false
        try {
            Get-CompactEvidence -RawContent $failingReport -RelPath "test/failing.md" `
                -RequiredKeys @("overall_result") -PassKeys @("overall_result") | Out-Null
        } catch { $threwOnFail = $true }
        if (-not $threwOnFail) { $failures.Add("Scenario 4: Get-CompactEvidence did not hard-fail on overall_result: FAIL.") }

        # 5: a report missing the machine-readable summary block entirely
        # hard-fails extraction - never silently returns an empty/partial
        # summary that could be mistaken for a real (passing) one.
        $noMarkerReport = "# Quality gate run`n`nSome prose with no machine-readable summary block at all.`n"
        $threwOnMissing = $false
        try {
            Get-CompactEvidence -RawContent $noMarkerReport -RelPath "test/no-marker.md" `
                -RequiredKeys @("overall_result") -PassKeys @("overall_result") | Out-Null
        } catch { $threwOnMissing = $true }
        if (-not $threwOnMissing) { $failures.Add("Scenario 5: Get-CompactEvidence did not hard-fail on a report with no machine-readable summary block.") }

        # 6: a report whose summary block is missing a REQUIRED key
        # hard-fails, even if every present key says PASS.
        $missingKeyReport = @(
            "## Machine-readable summary", "",
            '```',
            "overall_result: PASS",
            '```'
        ) -join "`n"
        $threwOnMissingKey = $false
        try {
            Get-CompactEvidence -RawContent $missingKeyReport -RelPath "test/missing-key.md" `
                -RequiredKeys @("overall_result", "backend_unit_test_count") -PassKeys @("overall_result") | Out-Null
        } catch { $threwOnMissingKey = $true }
        if (-not $threwOnMissingKey) { $failures.Add("Scenario 6: Get-CompactEvidence did not hard-fail on a summary block missing a required key.") }

        # 7: migration-shaped report - target revision and every PASS-gated
        # constraint result extract correctly.
        $migrationReport = @(
            "# Migration validation", "",
            "## Machine-readable summary", "",
            '```',
            "target_revision: 00e7f6cca017",
            "upgrade_result: PASS",
            "downgrade_result: PASS",
            "reupgrade_result: PASS",
            "provider_exclusion_constraint: PASS",
            "room_exclusion_constraint: PASS",
            '```'
        ) -join "`n"
        $migEvidence = Get-CompactEvidence -RawContent $migrationReport -RelPath "test/migration.md" `
            -RequiredKeys @("target_revision", "upgrade_result", "downgrade_result", "reupgrade_result", "provider_exclusion_constraint", "room_exclusion_constraint") `
            -PassKeys @("upgrade_result", "downgrade_result", "reupgrade_result", "provider_exclusion_constraint", "room_exclusion_constraint")
        if ($migEvidence.Fields["target_revision"] -ne "00e7f6cca017") { $failures.Add("Scenario 7: target_revision was not extracted correctly.") }

        # 8: a migration report with one failed step (downgrade_result:
        # FAIL) hard-fails, even though every other field says PASS.
        $migrationFailReport = $migrationReport -replace "downgrade_result: PASS", "downgrade_result: FAIL"
        $threwOnMigFail = $false
        try {
            Get-CompactEvidence -RawContent $migrationFailReport -RelPath "test/migration-fail.md" `
                -RequiredKeys @("downgrade_result") -PassKeys @("upgrade_result", "downgrade_result", "reupgrade_result") | Out-Null
        } catch { $threwOnMigFail = $true }
        if (-not $threwOnMigFail) { $failures.Add("Scenario 8: Get-CompactEvidence did not hard-fail on a migration report with a failed step.") }
    } finally {
        Remove-Item -Recurse -Force $tempDir -ErrorAction SilentlyContinue
    }

    if ($failures.Count -gt 0) {
        Write-Host "Compact-evidence validation FAILED:"
        foreach ($f in $failures) { Write-Host "  - $f" }
        exit 1
    }

    Write-Host "Compact-evidence validation PASSED (8 scenarios)."
    exit 0
}

if (-not (Test-CommandAvailable "codex")) {
    Write-Error "codex CLI not found on PATH. Install with: npm install -g @openai/codex"
    exit 1
}

Set-Location $root

$config = Get-WorkflowConfig
$packet = Build-ReviewPacket -RepoRoot $root -ProtectedBranches $config.protectedBranches `
    -Incremental:$Incremental -BaselineCommit $BaselineCommit -ToCommit $ToCommit `
    -ExcludeContextManifest:$ExcludeContextManifest -IncludeVerboseEvidence:$IncludeVerboseEvidence

if ($packet.Mode -eq "nothing-to-review") {
    Write-Host $packet.NothingToReviewReason
    exit 0
}
if ($packet.Mode -eq "clean-committed-range") {
    Write-Host "Working tree is clean - reviewing the committed range $($packet.DiffRangeArg[0]) instead."
}
if ($packet.Mode -eq "incremental") {
    Write-Host "Incremental review: baseline $($packet.DiffRangeArg[0]) -> current HEAD."
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
# Lowered from 1,300,000 (MED-004 repair, this round): that value was
# raised for legitimate reasons (see history below), but turned out to sit
# ABOVE a hard, external ceiling this script did not previously know
# about - Codex's own `turn/start` call rejects any input over 1,048,576
# characters outright (`Input exceeds the maximum length of 1048576
# characters`, observed directly in codex-review-stderr.txt), and that
# input is the packet PLUS the ~3,800-char prompt template PLUS several
# more thousand characters of overhead Codex's own CLI adds on top (schema
# serialization, sandbox/system instructions - not something this script
# controls or can measure in advance). 950,000 leaves comfortable headroom
# below that hard ceiling even accounting for that unmeasured overhead.
# This is a real ceiling, not a preference - it cannot be raised away; the
# only lever left is dropping/hint-pinning individual files (see
# $authTestCoverageHints below) exactly as already done for
# test_auth_api.py's split.
#
# History: originally 900,000. Raised to 1,300,000 in an earlier MED-004
# repair round when splitting test_auth_api.py alone wasn't enough to stop
# a long-running branch's ever-growing merge-base diff from dropping a
# DIFFERENT file each round - that reasoning is still correct in spirit,
# it just picked a number above a limit nobody had hit yet.
# ---------------------------------------------------------------------------
$MaxPacketChars = 950000
$sections = $packet.Sections

# Incremental mode's Tier 2 (fixed auth context, baseline evidence,
# frontend-unchanged evidence) must NOT be droppable the way FullBranch's
# Tier 2 (the full diff - now itself promoted to Tier 1, see
# Build-ReviewPacket) historically was - only Tier 3 is fair game in
# Incremental mode. FullBranch mode's threshold is unchanged.
$dropThreshold = if ($packet.Mode -eq "incremental") { 2 } else { 1 }

function Get-TotalChars {
    param([System.Collections.Generic.List[hashtable]]$Secs)
    $total = 0
    foreach ($s in $Secs) { $total += $s.Content.Length }
    return $total
}

$totalChars = Get-TotalChars -Secs $sections
if ($totalChars -gt $MaxPacketChars) {
    $droppable = $sections | Where-Object { $_.Tier -gt $dropThreshold } | Sort-Object -Property Tier -Descending
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
    Write-Host "Mode: $($packet.Mode). Reviewed-file candidates: $($packet.ReviewedCandidates.Count); omitted: $($packet.Omitted.Count)."
    Write-Host "Budget: $MaxPacketChars chars (drop threshold: Tier > $dropThreshold)."
    $tierGroups = $sections | Group-Object -Property Tier | Sort-Object -Property Name
    foreach ($g in $tierGroups) {
        $tierChars = ($g.Group | ForEach-Object { $_.Content.Length } | Measure-Object -Sum).Sum
        Write-Host "  Tier $($g.Name): $($g.Count) section(s), $tierChars chars."
    }
    $largest = $sections | Sort-Object -Property { $_.Content.Length } -Descending | Select-Object -First 5
    Write-Host "Largest 5 sections:"
    foreach ($s in $largest) {
        Write-Host "  $($s.Content.Length) chars - Tier $($s.Tier) - $($s.Title)"
    }
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
