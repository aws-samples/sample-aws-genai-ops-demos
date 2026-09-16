# Seed the teams DynamoDB table with sample team configurations.
# Usage: .\scripts\seed-teams.ps1 [-TableName <name>]

param(
    [string]$TableName = "health-analyzer-teams"
)

Write-Host "Seeding teams table: $TableName"

$teams = @(
    @{
        teamId = @{S = "payments"}
        teamName = @{S = "Payments Team"}
        email = @{S = "payments-oncall@example.com"}
        slackWebhookUrl = @{S = "<SLACK_WEBHOOK_URL_PAYMENTS>"}
        slackChannel = @{S = "#payments-alerts"}
        msTeamsWebhookUrl = @{S = "<MSTEAMS_WEBHOOK_URL_PAYMENTS>"}
        notifyOn = @{SS = @("CRITICAL", "HIGH", "MEDIUM")}
    },
    @{
        teamId = @{S = "data-team"}
        teamName = @{S = "Data & Analytics"}
        email = @{S = "data-team@example.com"}
        slackWebhookUrl = @{S = "<SLACK_WEBHOOK_URL_DATA>"}
        slackChannel = @{S = "#data-alerts"}
        notifyOn = @{SS = @("CRITICAL", "HIGH")}
    },
    @{
        teamId = @{S = "identity-team"}
        teamName = @{S = "Identity & Auth"}
        email = @{S = "identity-oncall@example.com"}
        slackWebhookUrl = @{S = "<SLACK_WEBHOOK_URL_IDENTITY>"}
        slackChannel = @{S = "#identity-alerts"}
        msTeamsWebhookUrl = @{S = "<MSTEAMS_WEBHOOK_URL_IDENTITY>"}
        notifyOn = @{SS = @("CRITICAL", "HIGH", "MEDIUM")}
    },
    @{
        teamId = @{S = "platform"}
        teamName = @{S = "Platform Engineering"}
        email = @{S = "platform@example.com"}
        slackWebhookUrl = @{S = "<SLACK_WEBHOOK_URL_PLATFORM>"}
        slackChannel = @{S = "#platform-ops"}
        msTeamsWebhookUrl = @{S = "<MSTEAMS_WEBHOOK_URL_PLATFORM>"}
        notifyOn = @{SS = @("CRITICAL", "HIGH", "MEDIUM", "LOW")}
    }
)

$tempFile = [System.IO.Path]::GetTempFileName()

foreach ($team in $teams) {
    $json = $team | ConvertTo-Json -Depth 5 -Compress
    [System.IO.File]::WriteAllText($tempFile, $json)

    $teamName = $team.teamId.S
    Write-Host "  Seeding team: $teamName" -ForegroundColor Cyan

    aws dynamodb put-item --table-name $TableName --item "file://$tempFile" --no-cli-pager

    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: Failed to seed team '$teamName'" -ForegroundColor Red
    }
}

Remove-Item $tempFile -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Done! Seeded $($teams.Count) teams." -ForegroundColor Green
