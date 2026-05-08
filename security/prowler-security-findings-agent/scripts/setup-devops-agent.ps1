function Invoke-SetupDevOpsAgent {
    param(
        [string]$AgentSpaceName = "prowler-security"
    )

    $DevOpsAgentRegion = if ($env:DEVOPS_AGENT_REGION) { $env:DEVOPS_AGENT_REGION } else { "us-east-1" }
    $AccountId = aws sts get-caller-identity --query Account --output text

    Write-Host "=============================================="
    Write-Host " DevOps Agent Setup - Prowler Security"
    Write-Host "=============================================="
    Write-Host "Account:          $AccountId"
    Write-Host "Agent Space Name: $AgentSpaceName"
    Write-Host "Region:           $DevOpsAgentRegion"
    Write-Host ""

    $ExistingSpaceId = aws devops-agent list-agent-spaces --region $DevOpsAgentRegion --query "agentSpaces[?name=='$AgentSpaceName'].agentSpaceId | [0]" --output text --no-cli-pager 2>$null
    if ($ExistingSpaceId -and $ExistingSpaceId -ne "None") {
        $AgentSpaceId = $ExistingSpaceId
        Write-Host "  Agent Space already exists: $AgentSpaceId"
    } else {
        $AgentSpaceId = aws devops-agent create-agent-space --name $AgentSpaceName --region $DevOpsAgentRegion --query 'agentSpace.agentSpaceId' --output text --no-cli-pager
        Write-Host "  Agent Space created: $AgentSpaceId"
        Start-Sleep -Seconds 10
    }
    $env:DEVOPS_AGENT_SPACE_ID = $AgentSpaceId

    $TrustDoc = @"
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "aidevops.amazonaws.com" },
    "Action": ["sts:AssumeRole", "sts:TagSession"],
    "Condition": { "StringEquals": { "aws:SourceAccount": "$AccountId" } }
  }]
}
"@
    $TrustFile = [System.IO.Path]::GetTempFileName()
    $TrustDoc | Out-File -FilePath $TrustFile -Encoding ascii

    foreach ($entry in @(
        @{ Name = "$AgentSpaceName-AgentSpaceRole"; Policy = "arn:aws:iam::aws:policy/AIDevOpsAgentAccessPolicy" },
        @{ Name = "$AgentSpaceName-OperatorRole"; Policy = "arn:aws:iam::aws:policy/AIDevOpsOperatorAppAccessPolicy" }
    )) {
        $roleName = $entry.Name
        aws iam get-role --role-name $roleName --no-cli-pager 2>$null
        if ($LASTEXITCODE -ne 0) {
            aws iam create-role --role-name $roleName --assume-role-policy-document "file://$TrustFile" --no-cli-pager | Out-Null
            aws iam attach-role-policy --role-name $roleName --policy-arn $entry.Policy --no-cli-pager | Out-Null
            Write-Host "  Created $roleName."
        }
    }
    $OperatorRoleArn = aws iam get-role --role-name "$AgentSpaceName-OperatorRole" --query 'Role.Arn' --output text --no-cli-pager
    $AgentSpaceRoleArn = aws iam get-role --role-name "$AgentSpaceName-AgentSpaceRole" --query 'Role.Arn' --output text --no-cli-pager

    Start-Sleep -Seconds 10
    aws devops-agent enable-operator-app --agent-space-id $AgentSpaceId --auth-flow iam --operator-app-role-arn $OperatorRoleArn --region $DevOpsAgentRegion --no-cli-pager 2>$null | Out-Null

    $ExistingAssoc = aws devops-agent list-associations --agent-space-id $AgentSpaceId --region $DevOpsAgentRegion --query "associations[?serviceId=='aws'].associationId | [0]" --output text --no-cli-pager 2>$null
    if (-not $ExistingAssoc -or $ExistingAssoc -eq "None") {
        $AssocConfig = "{`"aws`":{`"accountId`":`"$AccountId`",`"accountType`":`"monitor`",`"assumableRoleArn`":`"$AgentSpaceRoleArn`"}}"
        aws devops-agent associate-service --agent-space-id $AgentSpaceId --service-id aws --configuration $AssocConfig --region $DevOpsAgentRegion --no-cli-pager 2>$null | Out-Null
    }

    Write-Host ""
    Write-Host "  Webhook URL + secret:"
    Write-Host "  1. Open https://$DevOpsAgentRegion.console.aws.amazon.com/aidevops/home?region=$DevOpsAgentRegion#/agent-spaces/$AgentSpaceId"
    Write-Host "  2. Capabilities > Webhook > Add > Next > Generate URL and secret key"
    Write-Host ""
    $WebhookUrl = Read-Host "  Paste the webhook URL (or press Enter to skip)"
    if ($WebhookUrl) {
        $WebhookSecret = Read-Host "  Paste the webhook secret key"
        if ($WebhookSecret) {
            $env:DEVOPS_AGENT_WEBHOOK_URL = $WebhookUrl
            $env:DEVOPS_AGENT_WEBHOOK_SECRET = $WebhookSecret
        }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    Invoke-SetupDevOpsAgent @args
}
