# One-time (idempotent) GitHub repository setup: merge policy, labels, branch protection.
# Usage: pwsh scripts/setup-github.ps1 -Repo owner/name
param(
    [Parameter(Mandatory = $true)][string]$Repo
)
$ErrorActionPreference = 'Stop'

Write-Host "== Repository settings: squash-only merges, auto-delete branches, discussions"
gh repo edit $Repo --enable-squash-merge --enable-merge-commit=false --enable-rebase-merge=false `
    --delete-branch-on-merge --enable-discussions --enable-wiki=false

Write-Host "== Labels"
$labels = @(
    @{ name = 'feature';          color = '174B9B'; description = 'New functionality' },
    @{ name = 'bug';              color = 'D23B3B'; description = 'Something is broken' },
    @{ name = 'security';         color = 'B60205'; description = 'Security-sensitive change or issue' },
    @{ name = 'tech-debt';        color = '8D96A5'; description = 'Refactoring, cleanup' },
    @{ name = 'docs';             color = '0E8A16'; description = 'Documentation' },
    @{ name = 'area:api';         color = 'C5DEF5'; description = 'Backend' },
    @{ name = 'area:mobile';      color = 'C5DEF5'; description = 'Client app (iOS/Android/web)' },
    @{ name = 'area:admin';       color = 'C5DEF5'; description = 'Admin panel' },
    @{ name = 'area:emulator';    color = 'C5DEF5'; description = 'Car emulator' },
    @{ name = 'area:infra';       color = 'C5DEF5'; description = 'Docker, CI, hosting' },
    @{ name = 'ready';            color = '1E9E5A'; description = 'Has acceptance criteria, can be taken' },
    @{ name = 'blocked';          color = 'E59A1A'; description = 'Waiting for something' },
    @{ name = 'good first issue'; color = '7057FF'; description = 'Good for onboarding' }
)
foreach ($l in $labels) {
    gh label create $l.name --repo $Repo --color $l.color --description $l.description --force | Out-Null
}

Write-Host "== Branch protection for main"
$protection = @{
    required_status_checks        = @{ strict = $true; contexts = @('Secrets scan (gitleaks)', 'PR title follows Conventional Commits') }
    enforce_admins                = $false
    required_pull_request_reviews = @{ required_approving_review_count = 1; dismiss_stale_reviews = $true }
    restrictions                  = $null
    required_linear_history       = $true
    allow_force_pushes            = $false
    allow_deletions               = $false
    required_conversation_resolution = $true
} | ConvertTo-Json -Depth 5
$tmp = New-TemporaryFile
# No BOM: gh rejects JSON input that starts with one (Set-Content -Encoding utf8 adds it in PowerShell 5.1)
[IO.File]::WriteAllText($tmp, $protection, (New-Object System.Text.UTF8Encoding $false))
try {
    gh api -X PUT "repos/$Repo/branches/main/protection" --input $tmp | Out-Null
    Write-Host "   main is protected"
} catch {
    Write-Warning "Branch protection failed. Private repos need GitHub Pro (free with Student Pack) on the owner account."
} finally {
    Remove-Item $tmp
}

Write-Host "Done."
