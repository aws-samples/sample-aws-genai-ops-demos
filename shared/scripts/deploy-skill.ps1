# GenAI Ops Demo Library - Shared Agent Tools Skill Packager
#
# Fetches ONE skill (or custom agent) from the public Agent Tools repository
# (aws/tools-for-devops-agent) at a given ref and packages it for upload to an
# AWS DevOps Agent Agent Space.
#
# Design rule: one artefact, one home. The skill's source stays in the Agent Tools
# repository. This script fetches it into a temporary directory at deploy time and
# produces an upload zip; nothing is copied into this repository.
#
# The zip follows the Agent Tools upload rules: only the file extensions DevOps Agent
# accepts, and never the repo-only files (README, CHANGELOG, evals, eval config).
#
# Usage (from a demo directory):
#   & "..\..\shared\scripts\deploy-skill.ps1" -Skill eks-upgrade-readiness -Ref main
#   & "..\..\shared\scripts\deploy-skill.ps1" -CustomAgent aws-health-report -Ref v1.2.0
#
# Exports for the calling script:
#   $global:AGENT_TOOLS_SKILL_ZIP    Full path of the produced zip (skills only)
#   $global:AGENT_TOOLS_SKILL_DIR    Fetched source directory (temp, delete when done)

param(
    [string]$Skill = "",
    [string]$CustomAgent = "",
    [string]$Ref = "main",
    [string]$Repo = "https://github.com/aws/tools-for-devops-agent",
    # Where to write the zip. Defaults to the caller's current directory.
    [string]$OutputDirectory = ".",
    # Keep the fetched source directory instead of deleting it (debugging).
    [switch]$KeepSource = $false
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrEmpty($Skill) -and [string]::IsNullOrEmpty($CustomAgent)) {
    Write-Host "ERROR: Pass -Skill <name> or -CustomAgent <name>" -ForegroundColor Red
    exit 1
}
if (-not [string]::IsNullOrEmpty($Skill) -and -not [string]::IsNullOrEmpty($CustomAgent)) {
    Write-Host "ERROR: Pass either -Skill or -CustomAgent, not both" -ForegroundColor Red
    exit 1
}

$isSkill = -not [string]::IsNullOrEmpty($Skill)
$name = if ($isSkill) { $Skill } else { $CustomAgent }
$kindDir = if ($isSkill) { "skills" } else { "custom-agents" }
$sparsePath = "$kindDir/$name"

# Names are directory names in the upstream repo: lowercase, digits, hyphens only.
if ($name -notmatch '^[a-z0-9][a-z0-9-]{0,63}$') {
    Write-Host "ERROR: Invalid name '$name' (lowercase letters, digits and hyphens only)" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=== Agent Tools Skill Packager (Shared Script) ===" -ForegroundColor Cyan
Write-Host "      Repository: $Repo" -ForegroundColor Gray
Write-Host "      Path:       $sparsePath" -ForegroundColor Gray
Write-Host "      Ref:        $Ref" -ForegroundColor Gray

# Tooling
$null = Get-Command git -ErrorAction SilentlyContinue
if (-not $?) {
    Write-Host "ERROR: git is required. Install from https://git-scm.com" -ForegroundColor Red
    exit 1
}

# Fetch just the one directory, at the requested ref, into a temp dir.
# --filter=blob:none + --sparse downloads only the tree we ask for.
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("agent-tools-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $tempRoot | Out-Null

Write-Host ""
Write-Host "Fetching $sparsePath @ $Ref ..." -ForegroundColor Yellow
$prevErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
git clone --quiet --depth 1 --filter=blob:none --sparse --branch $Ref $Repo $tempRoot 2>&1 | Out-Null
$cloneExit = $LASTEXITCODE
if ($cloneExit -eq 0) {
    git -C $tempRoot sparse-checkout set $sparsePath 2>&1 | Out-Null
    $cloneExit = $LASTEXITCODE
}
$ErrorActionPreference = $prevErrorAction
if ($cloneExit -ne 0) {
    Write-Host "      ERROR: Could not fetch '$sparsePath' at ref '$Ref' from $Repo" -ForegroundColor Red
    Write-Host "      Check the ref exists (tag or branch) and the name is spelled correctly." -ForegroundColor Yellow
    Remove-Item -Recurse -Force $tempRoot -ErrorAction SilentlyContinue
    exit 1
}

$sourceDir = Join-Path $tempRoot $sparsePath
if (-not (Test-Path $sourceDir)) {
    Write-Host "      ERROR: '$sparsePath' does not exist at ref '$Ref'" -ForegroundColor Red
    Remove-Item -Recurse -Force $tempRoot -ErrorAction SilentlyContinue
    exit 1
}
$commit = (git -C $tempRoot rev-parse --short HEAD).Trim()
Write-Host "      OK: fetched $sparsePath @ $Ref ($commit)" -ForegroundColor Green
$global:AGENT_TOOLS_SKILL_DIR = $sourceDir

if ($isSkill) {
    if (-not (Test-Path (Join-Path $sourceDir "SKILL.md"))) {
        Write-Host "      ERROR: No SKILL.md in $sparsePath - not a skill directory" -ForegroundColor Red
        exit 1
    }

    # Build the upload zip per the Agent Tools upload rules.
    $allowedExtensions = @(".md", ".txt", ".json", ".yaml", ".yml", ".xml", ".csv", ".tsv",
                           ".html", ".htm", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf")
    $excludedFiles = @("README.md", "CHANGELOG.md", ".skilleval.yaml", ".skilleval.yml")
    $excludedDirs = @("evals", "scripts", ".claude")

    Write-Host ""
    Write-Host "Building upload zip..." -ForegroundColor Yellow
    $files = Get-ChildItem -Path $sourceDir -Recurse -File | Where-Object {
        $rel = $_.FullName.Substring($sourceDir.Length + 1)
        $segments = $rel -split '[\\/]'
        $inExcludedDir = ($segments[0..($segments.Length - 2)] | Where-Object { $excludedDirs -contains $_ }).Count -gt 0
        ($allowedExtensions -contains $_.Extension.ToLower()) -and
        ($excludedFiles -notcontains $_.Name) -and
        (-not $inExcludedDir)
    }
    if ($files.Count -eq 0) {
        Write-Host "      ERROR: Nothing to package after applying upload rules" -ForegroundColor Red
        exit 1
    }

    # Stage into <name>/... so the zip root is the skill directory, as DevOps Agent expects.
    $stageRoot = Join-Path $tempRoot "stage"
    $stageDir = Join-Path $stageRoot $name
    foreach ($f in $files) {
        $rel = $f.FullName.Substring($sourceDir.Length + 1)
        $dest = Join-Path $stageDir $rel
        New-Item -ItemType Directory -Path (Split-Path -Parent $dest) -Force | Out-Null
        Copy-Item $f.FullName $dest
    }

    $outDir = (Resolve-Path $OutputDirectory).Path
    $zipPath = Join-Path $outDir "$name.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    Compress-Archive -Path (Join-Path $stageRoot $name) -DestinationPath $zipPath
    $global:AGENT_TOOLS_SKILL_ZIP = $zipPath

    Write-Host "      OK: $($files.Count) files -> $zipPath" -ForegroundColor Green

    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Skill packaged: $name @ $Ref ($commit)" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Zip:      $zipPath" -ForegroundColor Cyan
    Write-Host "  Upload:   DevOps Agent console -> Agent Space -> Skills -> Upload" -ForegroundColor Cyan
    Write-Host "            Pick 'All agents' if a custom agent will use this skill." -ForegroundColor Gray
    Write-Host "  Source:   $Repo/tree/$Ref/$sparsePath" -ForegroundColor Cyan
} else {
    $promptPath = Join-Path $sourceDir "SYSTEM_PROMPT.md"
    if (-not (Test-Path $promptPath)) {
        Write-Host "      ERROR: No SYSTEM_PROMPT.md in $sparsePath - not a custom agent directory" -ForegroundColor Red
        exit 1
    }
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Custom agent fetched: $name @ $Ref ($commit)" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Prompt:   $promptPath" -ForegroundColor Cyan
    Write-Host "  Create:   DevOps Agent console -> Custom agents -> Create (Form)," -ForegroundColor Cyan
    Write-Host "            paste SYSTEM_PROMPT.md, then assign skills and tools." -ForegroundColor Gray
    Write-Host "  Details:  $Repo/tree/$Ref/$sparsePath/README.md" -ForegroundColor Cyan
    $KeepSource = $true   # the caller needs the prompt file
}

if (-not $KeepSource) {
    Remove-Item -Recurse -Force $tempRoot -ErrorAction SilentlyContinue
} else {
    Write-Host "  Source dir kept: $sourceDir" -ForegroundColor Gray
}
