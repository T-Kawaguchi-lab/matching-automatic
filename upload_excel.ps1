Add-Type -AssemblyName System.Windows.Forms

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

$incomingDir = Join-Path $repoRoot "incoming"
$targetFile = Join-Path $incomingDir "forms_latest.xlsx"

if (!(Test-Path $incomingDir)) {
    New-Item -ItemType Directory -Path $incomingDir | Out-Null
}

# Open file dialog
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = "Select Excel file to upload"
$dialog.Filter = "Excel files (*.xlsx)|*.xlsx"
$dialog.Multiselect = $false

$result = $dialog.ShowDialog()

if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
    Write-Host "Canceled."
    exit 0
}

$sourceFile = $dialog.FileName

if (!(Test-Path $sourceFile)) {
    Write-Host "Selected file not found."
    exit 1
}

Copy-Item -Path $sourceFile -Destination $targetFile -Force
Write-Host "Copied to incoming/forms_latest.xlsx"

# check git repo
git rev-parse --is-inside-work-tree *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Not inside a git repository."
    exit 1
}

git pull origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "git pull failed."
    exit 1
}

git add incoming/forms_latest.xlsx

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
git commit -m "Update forms_latest.xlsx ($timestamp)"

if ($LASTEXITCODE -ne 0) {
    Write-Host "Nothing to commit."
    exit 0
}

git push origin main

if ($LASTEXITCODE -ne 0) {
    Write-Host "git push failed."
    exit 1
}

Write-Host "Push completed. GitHub Actions should start automatically."
Pause