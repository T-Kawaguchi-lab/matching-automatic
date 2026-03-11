[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms

$ErrorActionPreference = "Stop"

function Fail-And-Pause {
    param([string]$Message)
    Write-Host ""
    Write-Host "ERROR: $Message" -ForegroundColor Red
    Write-Host ""
    pause
    exit 1
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

$incomingDir = Join-Path $repoRoot "incoming"
$targetFile = Join-Path $incomingDir "forms_latest.xlsx"

if (!(Test-Path $incomingDir)) {
    New-Item -ItemType Directory -Path $incomingDir | Out-Null
}

git rev-parse --is-inside-work-tree *> $null
if ($LASTEXITCODE -ne 0) {
    Fail-And-Pause "This folder is not a Git repository."
}
$remoteUrl = git remote get-url origin 2>$null
if ($LASTEXITCODE -ne 0) {
    Fail-And-Pause "origin remote is not configured."
}
if ($remoteUrl -notmatch "T-Kawaguchi-lab/matching-automatic") {
    Fail-And-Pause "This repository is not matching-automatic."
}

$currentBranch = git branch --show-current 2>$null
if ($LASTEXITCODE -ne 0) {
    Fail-And-Pause "Could not determine current branch."
}
if ($currentBranch.Trim() -ne "main") {
    Fail-And-Pause "Current branch is not main."
}

git ls-remote origin *> $null
if ($LASTEXITCODE -ne 0) {
    Fail-And-Pause "Cannot access GitHub remote. Please check GitHub authentication."
}
# Ignore changes to upload_excel.ps1 itself, but stop for other files
$statusLines = @(git status --porcelain)
if ($LASTEXITCODE -ne 0) {
    Fail-And-Pause "git status failed."
}

$blockingChanges = @()

foreach ($line in $statusLines) {
    if ([string]::IsNullOrWhiteSpace($line)) {
        continue
    }

    if ($line -match 'upload_excel\.ps1$') {
        continue
    }

    if ($line -match 'upload_excel\.bat$') {
        continue
    }

    $blockingChanges += $line
}

if ($blockingChanges.Count -gt 0) {
    Write-Host ""
    Write-Host "Working tree has other uncommitted changes." -ForegroundColor Yellow
    $blockingChanges | ForEach-Object { Write-Host $_ }
    Fail-And-Pause "Please commit, stash, or discard those changes first."
}

Write-Host "Fetching latest changes..." -ForegroundColor Yellow
git fetch origin main
if ($LASTEXITCODE -ne 0) {
    Fail-And-Pause "git fetch failed."
}

Write-Host "Pulling latest changes..." -ForegroundColor Yellow
git pull --rebase --autostash origin main
if ($LASTEXITCODE -ne 0) {
    Fail-And-Pause "git pull failed."
}

$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = "Select Excel file"
$dialog.Filter = "Excel files (*.xlsx)|*.xlsx"
$dialog.Multiselect = $false

$result = $dialog.ShowDialog()

if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
    Write-Host "Canceled."
    pause
    exit 0
}

$sourceFile = $dialog.FileName

if (!(Test-Path $sourceFile)) {
    Fail-And-Pause "Selected file was not found."
}

Copy-Item -Path $sourceFile -Destination $targetFile -Force
Write-Host "Copied to incoming/forms_latest.xlsx" -ForegroundColor Green

git add "incoming/forms_latest.xlsx"
if ($LASTEXITCODE -ne 0) {
    Fail-And-Pause "git add failed."
}

git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "No changes detected. Nothing to commit." -ForegroundColor Yellow
    pause
    exit 0
}

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$commitMessage = "Update forms_latest.xlsx ($timestamp)"

Write-Host "Creating commit..." -ForegroundColor Yellow
git commit -m $commitMessage
if ($LASTEXITCODE -ne 0) {
    Fail-And-Pause "git commit failed."
}

Write-Host "Pushing to GitHub..." -ForegroundColor Yellow
git push origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "First push failed. Retrying after pull..." -ForegroundColor Yellow

    git pull --rebase --autostash origin main
    if ($LASTEXITCODE -ne 0) {
        Fail-And-Pause "git pull before retry failed."
    }

    git push origin main
    if ($LASTEXITCODE -ne 0) {
        Fail-And-Pause "git push failed again."
    }
}

Write-Host ""
Write-Host "Push completed. GitHub Actions should start automatically." -ForegroundColor Green
Read-Host "Press Enter to continue"