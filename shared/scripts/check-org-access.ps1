# GenAI Ops Demo Library - Multi-account (hub-and-spoke) prerequisites check
#
# Read-only preflight for demos that scan several accounts of an AWS Organization
# from one "hub" account: the hub lists the organization's accounts and assumes a
# read-only role that a CloudFormation StackSet has placed in every member
# account ("spoke"). Three things must be true for that to work, and none of
# them can be fixed by a deploy script because they are management-account
# actions:
#
#   1. The hub can list the organization's accounts: it IS the management
#      account, or the management account delegated the Organizations read APIs
#      to it through the organization's resource-based policy.
#   2. Trusted access is enabled between AWS Organizations and CloudFormation
#      StackSets (service-managed permissions).
#   3. The hub may run service-managed StackSets: it is the management account
#      or a registered delegated administrator for StackSets.
#
# This script only reports; every FAIL comes with the exact command a
# management-account administrator has to run. Nothing is created or changed.
#
# Exit codes (so deploy scripts can branch):
#   0  multi-account scanning and StackSet rollout both possible
#   2  the hub cannot list accounts (multi-account mode impossible; use a manual
#      account list or fix item 1)
#   3  accounts can be listed but the hub cannot roll the spoke role out itself
#      (deploy anyway, have the management account run the StackSet once)
#   1  no AWS credentials / not in an organization / unexpected error
#
# Usage:
#   & "..\..\shared\scripts\check-org-access.ps1"           # human-readable report
#   & "..\..\shared\scripts\check-org-access.ps1" -Json     # machine-readable only
#
# Exports for the calling script: $global:ORG_ID, $global:ORG_MANAGEMENT_ACCOUNT_ID,
# $global:ORG_IS_MANAGEMENT, $global:ORG_CAN_LIST_ACCOUNTS, $global:ORG_CAN_ROLLOUT_STACKSETS

param(
    [switch]$Json = $false
)

$StackSetsPrincipal = "member.org.stacksets.cloudformation.amazonaws.com"

# Organizations read APIs a hub needs (used in the fix command). Two of them let
# the hub verify items 2 and 3 itself.
$OrgReadActions = @(
    "organizations:DescribeOrganization",
    "organizations:ListAccounts",
    "organizations:DescribeAccount",
    "organizations:ListRoots",
    "organizations:ListOrganizationalUnitsForParent",
    "organizations:ListParents",
    "organizations:DescribeOrganizationalUnit",
    "organizations:ListTagsForResource",
    "organizations:ListAWSServiceAccessForOrganization",
    "organizations:ListDelegatedAdministrators"
)

function Write-Line($text, $color) { if (-not $Json) { Write-Host $text -ForegroundColor $color } }

# Run an AWS CLI command; return @{ ok; json; error } without ever throwing.
function Invoke-Aws([string[]]$cmdArgs) {
    $out = & aws @cmdArgs --output json --no-cli-pager 2>&1
    $text = ($out | Out-String).Trim()
    if ($LASTEXITCODE -eq 0) {
        return @{ ok = $true; json = ($text | ConvertFrom-Json); error = "" }
    }
    return @{ ok = $false; json = $null; error = $text }
}

function Error-Code([string]$err) {
    if ($err -match '\((\w+Exception)\)') { return $Matches[1] }
    return "Error"
}

$result = [ordered]@{
    account_id             = $null
    caller_arn             = $null
    organization_id        = $null
    management_account_id  = $null
    is_management_account  = $false
    can_list_accounts      = $false
    stacksets_trusted_access = "unknown"   # true | false | unknown
    can_rollout_stacksets  = $false
    checks                 = @()
    exit_code              = 1
}

function Add-Check($name, $status, $detail, $fix) {
    $script:result.checks += [ordered]@{ name = $name; status = $status; detail = $detail; fix = $fix }
    $color = switch ($status) { "OK" { "Green" } "FAIL" { "Red" } "WARN" { "Yellow" } default { "Gray" } }
    Write-Line "      $status`: $name - $detail" $color
    if ($fix) { Write-Line "      Fix (management account): $fix" "Cyan" }
}

Write-Line "=== Multi-account (hub-and-spoke) prerequisites check (Shared Script) ===" "Cyan"

# --- Who am I ---------------------------------------------------------------
Write-Line "`nIdentifying the hub account..." "Yellow"
$who = Invoke-Aws @("sts", "get-caller-identity")
if (-not $who.ok) {
    Write-Line "      ERROR: AWS credentials are not configured or have expired" "Red"
    if ($Json) { $result | ConvertTo-Json -Depth 5 }
    exit 1
}
$result.account_id = $who.json.Account
$result.caller_arn = $who.json.Arn
Write-Line "      Hub account: $($result.account_id) ($($result.caller_arn))" "Green"

# --- In an organization? Management account? --------------------------------
Write-Line "`nChecking AWS Organizations membership..." "Yellow"
$org = Invoke-Aws @("organizations", "describe-organization")
if ($org.ok) {
    $result.organization_id = $org.json.Organization.Id
    $result.management_account_id = $org.json.Organization.MasterAccountId
    $result.is_management_account = ($result.management_account_id -eq $result.account_id)
    if ($result.is_management_account) {
        Add-Check "Organization membership" "OK" "organization $($result.organization_id); this account is the MANAGEMENT account" $null
        Write-Line "      INFO: AWS recommends keeping workloads out of the management account; a member account as hub is the safer choice" "Gray"
    } else {
        Add-Check "Organization membership" "OK" "organization $($result.organization_id), management account $($result.management_account_id)" $null
    }
} else {
    $code = Error-Code $org.error
    if ($code -eq "AWSOrganizationsNotInUseException") {
        Add-Check "Organization membership" "FAIL" "this account is not part of an AWS Organization" "Multi-account scanning needs an organization; single-account mode still works"
        $result.exit_code = 1
        if ($Json) { $result | ConvertTo-Json -Depth 5 } else { Write-Line "`nResult: single-account only." "Yellow" }
        exit 1
    }
    # AccessDenied: a member account without the resource-policy delegation
    Add-Check "Organization membership" "WARN" "cannot read the organization ($code); assuming member account" $null
}

# The fix for item 1, with this account's id filled in
$policyDoc = @{
    Version = "2012-10-17"
    Statement = @(@{
        Sid = "GenAiOpsDemoHubReadsOrganization"
        Effect = "Allow"
        Principal = @{ AWS = "arn:aws:iam::$($result.account_id):root" }
        Action = $OrgReadActions
        Resource = "*"
    })
} | ConvertTo-Json -Depth 5 -Compress
$fixListAccounts = "aws organizations put-resource-policy --content '$policyDoc'"

# --- 1. Can the hub list accounts? -----------------------------------------
Write-Line "`nChecking account listing (organizations:ListAccounts)..." "Yellow"
$list = Invoke-Aws @("organizations", "list-accounts", "--max-items", "1")
if ($list.ok) {
    $result.can_list_accounts = $true
    $how = if ($result.is_management_account) { "management account" } else { "delegated through the organization resource policy" }
    Add-Check "Account listing" "OK" "ListAccounts allowed ($how)" $null
} else {
    Add-Check "Account listing" "FAIL" "ListAccounts denied ($(Error-Code $list.error))" $fixListAccounts
    Write-Line "      Docs: https://docs.aws.amazon.com/organizations/latest/userguide/orgs_delegate_policies.html" "Gray"
}

# --- 2. Trusted access for StackSets ---------------------------------------
Write-Line "`nChecking CloudFormation StackSets trusted access..." "Yellow"
$svc = Invoke-Aws @("organizations", "list-aws-service-access-for-organization")
if ($svc.ok) {
    $enabled = @($svc.json.EnabledServicePrincipals | Where-Object { $_.ServicePrincipal -eq $StackSetsPrincipal }).Count -gt 0
    $result.stacksets_trusted_access = $enabled
    if ($enabled) {
        Add-Check "StackSets trusted access" "OK" "service-managed StackSets enabled for the organization" $null
    } else {
        Add-Check "StackSets trusted access" "FAIL" "trusted access not enabled for $StackSetsPrincipal" "aws organizations enable-aws-service-access --service-principal $StackSetsPrincipal"
    }
} else {
    Add-Check "StackSets trusted access" "WARN" "cannot verify ($(Error-Code $svc.error)); needs organizations:ListAWSServiceAccessForOrganization" $null
}

# --- 3. Can the hub run service-managed StackSets? --------------------------
Write-Line "`nChecking StackSets delegated administrator..." "Yellow"
if ($result.is_management_account) {
    $result.can_rollout_stacksets = ($result.stacksets_trusted_access -eq $true)
    if ($result.can_rollout_stacksets) {
        Add-Check "StackSets rollout" "OK" "management account can create service-managed StackSets" $null
    } else {
        Add-Check "StackSets rollout" "FAIL" "blocked until trusted access is enabled (see above)" $null
    }
} else {
    $del = Invoke-Aws @("organizations", "list-delegated-administrators", "--service-principal", $StackSetsPrincipal)
    if ($del.ok) {
        $isDelegated = @($del.json.DelegatedAdministrators | Where-Object { $_.Id -eq $result.account_id }).Count -gt 0
        $result.can_rollout_stacksets = $isDelegated -and ($result.stacksets_trusted_access -ne $false)
        if ($isDelegated) {
            Add-Check "StackSets rollout" "OK" "this account is a delegated administrator for StackSets" $null
        } else {
            Add-Check "StackSets rollout" "FAIL" "this account is not a StackSets delegated administrator" "aws organizations register-delegated-administrator --account-id $($result.account_id) --service-principal $StackSetsPrincipal"
            Write-Line "      Docs: https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/stacksets-orgs-delegated-admin.html" "Gray"
        }
    } else {
        Add-Check "StackSets rollout" "WARN" "cannot verify ($(Error-Code $del.error)); needs organizations:ListDelegatedAdministrators" $null
    }
}

# --- Verdict ----------------------------------------------------------------
if (-not $result.can_list_accounts) {
    $result.exit_code = 2
    Write-Line "`nResult: multi-account scanning NOT possible from this account (cannot list accounts)." "Red"
    Write-Line "        Either run the fix above in the management account, or use a manual account list." "Yellow"
} elseif (-not $result.can_rollout_stacksets) {
    $result.exit_code = 3
    Write-Line "`nResult: accounts can be listed, but this account cannot roll the spoke role out itself." "Yellow"
    Write-Line "        Deploy the demo; then have the management account deploy the spoke role StackSet once (see the demo README)." "Yellow"
} else {
    $result.exit_code = 0
    Write-Line "`nResult: multi-account scanning and StackSet rollout are both possible from this account." "Green"
}

if ($Json) { $result | ConvertTo-Json -Depth 5 }

$global:ORG_ID = $result.organization_id
$global:ORG_MANAGEMENT_ACCOUNT_ID = $result.management_account_id
$global:ORG_IS_MANAGEMENT = $result.is_management_account
$global:ORG_CAN_LIST_ACCOUNTS = $result.can_list_accounts
$global:ORG_CAN_ROLLOUT_STACKSETS = $result.can_rollout_stacksets

exit $result.exit_code
