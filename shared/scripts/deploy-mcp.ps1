# GenAI Ops Demo Library - Shared Agent Tools MCP Server Runner
#
# Deploys ONE MCP server from the public Agent Tools repository
# (aws/tools-for-devops-agent) into the current AWS account, driven entirely by the
# server's `mcp-server.yaml` manifest (MCP Server Integration Contract v1).
#
# Design rule: one artefact, one home. The server's source stays in the Agent Tools
# repository. This script fetches it into a temp dir at deploy time, runs the deploy
# command the manifest declares, captures the endpoint output the manifest names,
# and reports how to register it with AWS DevOps Agent. Nothing is copied into this
# repository, and this script never reads the server's internals.
#
# Manifest resolution: `mcp/<name>/mcp-server.yaml` in the fetched source — that is the
# only sanctioned home. If a server has no manifest upstream yet, a demo may pass
# -Manifest <path> to a temporary one kept in its OWN folder (never in shared/).
#
# Usage (from a demo directory):
#   & "..\..\shared\scripts\deploy-mcp.ps1" -Server aws-vpc-dns-diagnostics-mcp -Ref main `
#         -Parameters @{ AllowedAccounts = "111111111111" }
#   & "..\..\shared\scripts\deploy-mcp.ps1" -Server aws-vpc-dns-diagnostics-mcp -Destroy -Manifest ...
#
# Exports for the calling script:
#   $global:AGENT_TOOLS_MCP_ENDPOINT          Full MCP endpoint URL (path applied)
#   $global:AGENT_TOOLS_MCP_AUTH_METHOD       e.g. sigv4
#   $global:AGENT_TOOLS_MCP_SIGNING_SERVICE   e.g. lambda | execute-api (sigv4 only)
#   $global:AGENT_TOOLS_MCP_STACK             Main stack name

param(
    [Parameter(Mandatory=$true)]
    [string]$Server,
    [string]$Ref = "main",
    [string]$Repo = "https://github.com/aws/tools-for-devops-agent",
    # Local manifest override (path). Used until the server ships mcp-server.yaml upstream.
    [string]$Manifest = "",
    # Values for the manifest's declared deploy parameters (substituted as ${Name}).
    [hashtable]$Parameters = @{},
    [switch]$Destroy = $false,
    [switch]$KeepSource = $false
)

$ErrorActionPreference = "Stop"

if ($Server -notmatch '^[a-z0-9][a-z0-9-]{0,63}$') {
    Write-Host "ERROR: Invalid server name '$Server' (lowercase letters, digits and hyphens only)" -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Minimal YAML reader for the manifest subset: nested mappings, scalars,
# flow-style lists ([a, b]), and block lists of scalars or of mappings.
# No comments-in-values, no anchors, no multi-line scalars - the contract keeps
# manifests to this subset on purpose so consumers need no YAML dependency.
# ---------------------------------------------------------------------------
function ConvertFrom-ManifestYaml {
    param([string[]]$Lines)

    function Parse-Scalar([string]$s) {
        $s = $s.Trim()
        if ($s -match '^"(.*)"$' -or $s -match "^'(.*)'$") { return $Matches[1] }
        if ($s -eq 'true') { return $true }
        if ($s -eq 'false') { return $false }
        if ($s -eq 'null' -or $s -eq '~' -or $s -eq '') { return $null }
        if ($s -match '^-?\d+$') { return [int]$s }
        return $s
    }
    function Parse-FlowList([string]$s) {
        $inner = $s.Trim().TrimStart('[').TrimEnd(']')
        if ($inner.Trim() -eq '') { return @() }
        $items = @()
        foreach ($part in ($inner -split ',')) { $items += Parse-Scalar $part }
        return ,$items   # comma keeps a 1-element list a list (PowerShell unrolls otherwise)
    }

    # Strip comments and blank lines, keep indentation.
    $clean = @()
    foreach ($raw in $Lines) {
        $line = $raw -replace '\s+#.*$', ''
        if ($line -match '^\s*#') { continue }
        if ($line.Trim() -eq '') { continue }
        $clean += $line.TrimEnd()
    }

    $script:idx = 0
    function Indent([string]$l) { return ($l.Length - $l.TrimStart().Length) }

    function Parse-Block([int]$indent) {
        # Decide list vs mapping from the first line.
        if ($script:idx -ge $clean.Count) { return $null }
        $first = $clean[$script:idx]
        if ((Indent $first) -ne $indent) { return $null }
        if ($first.TrimStart().StartsWith('- ')) { return Parse-List $indent }
        return Parse-Mapping $indent
    }

    function Parse-Mapping([int]$indent) {
        $map = [ordered]@{}
        while ($script:idx -lt $clean.Count) {
            $line = $clean[$script:idx]
            $ind = Indent $line
            if ($ind -lt $indent) { break }
            if ($ind -gt $indent) { throw "Manifest parse error near: '$line'" }
            if ($line.TrimStart().StartsWith('- ')) { break }
            if ($line -notmatch '^\s*([A-Za-z0-9_.-]+)\s*:\s*(.*)$') { throw "Manifest parse error near: '$line'" }
            $key = $Matches[1]; $rest = $Matches[2]
            $script:idx++
            if ($rest -ne '') {
                if ($rest.TrimStart().StartsWith('[')) { $map[$key] = Parse-FlowList $rest }
                else { $map[$key] = Parse-Scalar $rest }
            } else {
                if ($script:idx -lt $clean.Count -and (Indent $clean[$script:idx]) -gt $indent) {
                    $map[$key] = Parse-Block (Indent $clean[$script:idx])
                } else {
                    $map[$key] = $null
                }
            }
        }
        return $map
    }

    function Parse-List([int]$indent) {
        $list = @()
        while ($script:idx -lt $clean.Count) {
            $line = $clean[$script:idx]
            $ind = Indent $line
            if ($ind -ne $indent -or -not $line.TrimStart().StartsWith('- ')) { break }
            $content = $line.TrimStart().Substring(2)
            $script:idx++
            if ($content -match '^([A-Za-z0-9_.-]+)\s*:\s*(.*)$') {
                # List item is a mapping; rewrite first line as an indented mapping and parse it.
                $itemIndent = $indent + 2
                $rewritten = (' ' * $itemIndent) + $content
                $clean[$script:idx - 1] = $rewritten
                $script:idx--
                $list += ,(Parse-Mapping $itemIndent)
            } elseif ($content.TrimStart().StartsWith('[')) {
                $list += ,(Parse-FlowList $content)
            } else {
                $list += Parse-Scalar $content
            }
        }
        return ,$list
    }

    return Parse-Block (Indent $clean[0])
}

function Get-Required($map, [string]$path) {
    $cur = $map
    foreach ($seg in $path.Split('.')) {
        if ($null -eq $cur -or -not $cur.Contains($seg)) {
            Write-Host "ERROR: manifest is missing required field '$path'" -ForegroundColor Red
            exit 1
        }
        $cur = $cur[$seg]
    }
    return $cur
}
function Get-Optional($map, [string]$path, $default = $null) {
    $cur = $map
    foreach ($seg in $path.Split('.')) {
        if ($null -eq $cur -or -not $cur.Contains($seg)) { return $default }
        $cur = $cur[$seg]
    }
    if ($null -eq $cur) { return $default }
    return $cur
}

# Substitute ${Name} with -Parameters values in every argv element.
function Expand-Argv([object[]]$argv, [hashtable]$values) {
    $out = @()
    foreach ($a in $argv) {
        $s = [string]$a
        $s = [regex]::Replace($s, '\$\{([A-Za-z0-9_]+)\}', {
            param($m)
            $k = $m.Groups[1].Value
            if ($values.ContainsKey($k)) { return [string]$values[$k] }
            Write-Host "ERROR: deploy command needs parameter '$k' - pass -Parameters @{ $k = '...' }" -ForegroundColor Red
            exit 1
        })
        $out += $s
    }
    return $out
}

function Invoke-Argv([object[]]$argv, [string]$workdir, [string]$label) {
    $exe = [string]$argv[0]
    $rest = @()
    if ($argv.Count -gt 1) { $rest = $argv[1..($argv.Count - 1)] }
    Write-Host "      > $exe $($rest -join ' ')" -ForegroundColor Gray
    Push-Location $workdir
    try {
        $prevErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & $exe @rest
        $code = $LASTEXITCODE
        $ErrorActionPreference = $prevErrorAction
    } finally { Pop-Location }
    if ($code -ne 0) {
        Write-Host "      ERROR: $label failed (exit $code)" -ForegroundColor Red
        exit 1
    }
}

# ---------------------------------------------------------------------------
# Region / account (same priority as the rest of the repo)
# ---------------------------------------------------------------------------
$accountId = aws sts get-caller-identity --query Account --output text --no-cli-pager
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: AWS credentials are not configured or have expired" -ForegroundColor Red
    exit 1
}
$currentRegion = if (-not [string]::IsNullOrEmpty($env:AWS_REGION)) { $env:AWS_REGION }
                 elseif (-not [string]::IsNullOrEmpty($env:AWS_DEFAULT_REGION)) { $env:AWS_DEFAULT_REGION }
                 else { aws configure get region }
if ([string]::IsNullOrEmpty($currentRegion)) {
    Write-Host "ERROR: No AWS region configured" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=== Agent Tools MCP Server Runner (Shared Script) ===" -ForegroundColor Cyan
Write-Host "      Server:  $Server @ $Ref" -ForegroundColor Gray
Write-Host "      Region:  $currentRegion" -ForegroundColor Gray
Write-Host "      Account: $accountId" -ForegroundColor Gray
Write-Host "      Mode:    $(if ($Destroy) { 'destroy' } else { 'deploy' })" -ForegroundColor Gray

# ---------------------------------------------------------------------------
# Fetch mcp/<server> at the ref
# ---------------------------------------------------------------------------
$sparsePath = "mcp/$Server"
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
$sourceDir = Join-Path $tempRoot $sparsePath
if ($cloneExit -ne 0 -or -not (Test-Path $sourceDir)) {
    Write-Host "      ERROR: Could not fetch '$sparsePath' at ref '$Ref' from $Repo" -ForegroundColor Red
    Remove-Item -Recurse -Force $tempRoot -ErrorAction SilentlyContinue
    exit 1
}
$commit = (git -C $tempRoot rev-parse --short HEAD).Trim()
Write-Host "      OK: fetched $sparsePath @ $Ref ($commit)" -ForegroundColor Green

# ---------------------------------------------------------------------------
# Manifest: upstream first, local override second
# ---------------------------------------------------------------------------
$manifestPath = Join-Path $sourceDir "mcp-server.yaml"
$manifestOrigin = "upstream"
if (-not [string]::IsNullOrEmpty($Manifest)) {
    $manifestPath = (Resolve-Path $Manifest).Path
    $manifestOrigin = "local override"
}
if (-not (Test-Path $manifestPath)) {
    Write-Host "      ERROR: No mcp-server.yaml for '$Server' at ref '$Ref'." -ForegroundColor Red
    Write-Host "      This server does not ship a manifest yet. Either get one added upstream," -ForegroundColor Yellow
    Write-Host "      or pass -Manifest <path> to a temporary one in your demo's own folder." -ForegroundColor Yellow
    Write-Host "      See shared/agent-tools/README.md for the schema." -ForegroundColor Yellow
    Remove-Item -Recurse -Force $tempRoot -ErrorAction SilentlyContinue
    exit 1
}

$m = ConvertFrom-ManifestYaml (Get-Content $manifestPath)
Write-Host "      OK: manifest ($manifestOrigin) $manifestPath" -ForegroundColor Green

$schemaVersion = Get-Required $m 'schemaVersion'
if ($schemaVersion -ne 1) {
    Write-Host "      ERROR: manifest schemaVersion $schemaVersion is not supported (this runner speaks v1)" -ForegroundColor Red
    exit 1
}
$manifestName = Get-Required $m 'name'
if ($manifestName -ne $Server) {
    Write-Host "      ERROR: manifest name '$manifestName' does not match server '$Server'" -ForegroundColor Red
    exit 1
}
$kind = Get-Required $m 'kind'
$iac  = Get-Optional $m 'iac' 'unknown'
$workdir = Join-Path $sourceDir (Get-Optional $m 'deploy.workdir' '.')

# Tooling the declared IaC needs (only what we can check generically).
$toolFor = @{ sam = 'sam'; cdk = 'npx'; cfn = 'aws'; terraform = 'terraform' }
if ($toolFor.ContainsKey($iac)) {
    if (-not (Get-Command $toolFor[$iac] -ErrorAction SilentlyContinue)) {
        Write-Host "      ERROR: manifest declares iac '$iac' but '$($toolFor[$iac])' is not installed" -ForegroundColor Red
        exit 1
    }
}

# Required parameters declared by the manifest must be supplied.
foreach ($p in @(Get-Optional $m 'deploy.parameters' @())) {
    if ((Get-Optional $p 'required' $false) -and -not $Parameters.ContainsKey($p['name'])) {
        Write-Host "      ERROR: required parameter '$($p['name'])' not supplied - $($p['description'])" -ForegroundColor Red
        Write-Host "      Pass: -Parameters @{ $($p['name']) = '...' }" -ForegroundColor Yellow
        exit 1
    }
}

$mainStack = @(Get-Optional $m 'deploy.stacks' @()) | Select-Object -First 1

# ---------------------------------------------------------------------------
# Destroy
# ---------------------------------------------------------------------------
if ($Destroy) {
    $teardown = Get-Required $m 'teardown.command'
    Write-Host ""
    Write-Host "Tearing down ($iac)..." -ForegroundColor Yellow
    Invoke-Argv (Expand-Argv $teardown $Parameters) $workdir "teardown"
    Write-Host "      OK: teardown command completed" -ForegroundColor Green
    $residuals = @(Get-Optional $m 'teardown.residuals' @())
    if ($residuals.Count -gt 0) {
        Write-Host ""
        Write-Host "  Not removed by the teardown command - clean up manually:" -ForegroundColor Yellow
        foreach ($r in $residuals) { Write-Host "    - $r" -ForegroundColor Yellow }
    }
    if (-not $KeepSource) { Remove-Item -Recurse -Force $tempRoot -ErrorAction SilentlyContinue }
    exit 0
}

# ---------------------------------------------------------------------------
# Deploy
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Deploying ($iac)..." -ForegroundColor Yellow
foreach ($step in @(Get-Optional $m 'deploy.setup' @())) {
    Invoke-Argv (Expand-Argv $step $Parameters) $workdir "setup step"
}
$deployCmd = Get-Required $m 'deploy.command'
$winCmd = Get-Optional $m 'deploy.windows'
if ($null -ne $winCmd -and $IsWindows) { $deployCmd = $winCmd }
Invoke-Argv (Expand-Argv $deployCmd $Parameters) $workdir "deploy"
Write-Host "      OK: deploy command completed" -ForegroundColor Green

$aux = @(Get-Optional $m 'deploy.auxiliaryStacks' @())

# Pattern C: nothing to host - the template registered the connection itself.
if ($kind -eq 'aws-hosted-reference') {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Connection deployed: $Server @ $Ref ($commit)" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  This server is AWS-hosted; the template registered it with DevOps Agent." -ForegroundColor Cyan
    Write-Host "  Associate the registered service with your Agent Space to finish." -ForegroundColor Cyan
    if (-not $KeepSource) { Remove-Item -Recurse -Force $tempRoot -ErrorAction SilentlyContinue }
    exit 0
}

# Endpoint from the declared stack output
$outputName = Get-Required $m 'endpoint.output'
$outputStack = Get-Optional $m 'endpoint.stack' $mainStack
if ([string]::IsNullOrEmpty($outputStack)) {
    Write-Host "      ERROR: manifest names no stack to read '$outputName' from (endpoint.stack / deploy.stacks)" -ForegroundColor Red
    exit 1
}
Write-Host ""
Write-Host "Reading endpoint output '$outputName' from stack '$outputStack'..." -ForegroundColor Yellow
$endpoint = aws cloudformation describe-stacks --stack-name $outputStack --region $currentRegion `
    --query "Stacks[0].Outputs[?OutputKey=='$outputName'].OutputValue | [0]" --output text --no-cli-pager 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrEmpty($endpoint) -or $endpoint -eq 'None') {
    Write-Host "      ERROR: output '$outputName' not found on stack '$outputStack'" -ForegroundColor Red
    exit 1
}
$includesPath = Get-Optional $m 'endpoint.includesMcpPath' $true
if (-not $includesPath) { $endpoint = $endpoint.TrimEnd('/') + '/mcp' }   # exactly /mcp - no trailing slash
Write-Host "      OK: $endpoint" -ForegroundColor Green

$authMethod = Get-Required $m 'auth.method'
$signingService = Get-Optional $m 'auth.signingService'
$callerActions = @(Get-Optional $m 'auth.callerActions' @())

$global:AGENT_TOOLS_MCP_ENDPOINT = $endpoint
$global:AGENT_TOOLS_MCP_AUTH_METHOD = $authMethod
$global:AGENT_TOOLS_MCP_SIGNING_SERVICE = $signingService
$global:AGENT_TOOLS_MCP_STACK = $outputStack

# Optional smoke test
$smoke = Get-Optional $m 'smokeTest'
if ($null -ne $smoke) {
    Write-Host ""
    Write-Host "Running smoke test..." -ForegroundColor Yellow
    Invoke-Argv (Expand-Argv $smoke $Parameters) $workdir "smoke test"
    Write-Host "      OK: smoke test passed" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Summary + registration next step
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  MCP server deployed: $Server @ $Ref ($commit)" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Endpoint: $endpoint" -ForegroundColor Cyan
Write-Host "  Auth:     $authMethod$(if ($signingService) { " (signing service: $signingService)" })" -ForegroundColor Cyan
Write-Host "  Stack:    $outputStack ($currentRegion)" -ForegroundColor Cyan
if ($aux.Count -gt 0) {
    Write-Host ""
    Write-Host "  Also required (not deployed by this script):" -ForegroundColor Yellow
    foreach ($a in $aux) { Write-Host "    - $($a['template']) [$($a['scope'])]" -ForegroundColor Yellow }
}
Write-Host ""
Write-Host "  Register with AWS DevOps Agent:" -ForegroundColor Cyan
switch ($authMethod) {
    'sigv4' {
        Write-Host "    1. Create/choose an IAM role trusted by aidevops.amazonaws.com with these actions:" -ForegroundColor Gray
        foreach ($a in $callerActions) { Write-Host "         - $a" -ForegroundColor Gray }
        Write-Host "    2. aws devops-agent register-service --region <agent-space-region> --service mcpserversigv4 \" -ForegroundColor Gray
        Write-Host "         --service-details '{`"mcpserversigv4`":{`"name`":`"$Server`",`"endpoint`":`"$endpoint`"," -ForegroundColor Gray
        Write-Host "         `"authorizationConfig`":{`"region`":`"$currentRegion`",`"service`":`"$signingService`",`"mcpRoleArn`":`"<role-arn>`"}}}'" -ForegroundColor Gray
        Write-Host "    3. Associate the returned serviceId with your Agent Space and allowlist tools." -ForegroundColor Gray
    }
    'oauth-client-credentials' {
        Write-Host "    Console -> Capability Providers -> MCP Server -> OAuth Client Credentials." -ForegroundColor Gray
        Write-Host "    Client ID / token URL / scope come from the stack outputs named in the manifest;" -ForegroundColor Gray
        Write-Host "    retrieve the client secret with the manifest's clientSecret command." -ForegroundColor Gray
    }
    default {
        Write-Host "    Console -> Capability Providers -> MCP Server -> $authMethod." -ForegroundColor Gray
    }
}
Write-Host "  Details:  $Repo/tree/$Ref/$sparsePath/README.md" -ForegroundColor Cyan

if (-not $KeepSource) { Remove-Item -Recurse -Force $tempRoot -ErrorAction SilentlyContinue }
