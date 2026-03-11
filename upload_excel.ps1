Add-Type -AssemblyName System.Windows.Forms

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

$incomingDir = Join-Path $repoRoot "incoming"
$targetFile = Join-Path $incomingDir "forms_latest.xlsx"

if (!(Test-Path $incomingDir)) {
    New-Item -ItemType Directory -Path $incomingDir | Out-Null
}

function Fail-And-Pause($message) {
    Write-Host ""
    Write-Host "ERROR: $message" -ForegroundColor Red
    Write-Host ""
    Pause
    exit 1
}

function Remove-StaleRebaseState {
    $rebaseMerge = Join-Path $repoRoot ".git\rebase-merge"
    $rebaseApply = Join-Path $repoRoot ".git\rebase-apply"

    if (Test-Path $rebaseMerge) {
        try {
            Remove-Item -Recurse -Force $rebaseMerge
            Write-Host "Removed stale .git/rebase-merge"
        } catch {
            Fail-And-Pause "古い rebase 状態 (.git/rebase-merge) を削除できませんでした。OneDrive同期・VS Code・Explorerを閉じて再実行してください。"
        }
    }

    if (Test-Path $rebaseApply) {
        try {
            Remove-Item -Recurse -Force $rebaseApply
            Write-Host "Removed stale .git/rebase-apply"
        } catch {
            Fail-And-Pause "古い rebase 状態 (.git/rebase-apply) を削除できませんでした。OneDrive同期・VS Code・Explorerを閉じて再実行してください。"
        }
    }
}

function Ensure-GitRepo {
    git rev-parse --is-inside-work-tree *> $null
    if ($LASTEXITCODE -ne 0) {
        Fail-And-Pause "このフォルダは Git リポジトリではありません。"
    }
}

function Ensure-CleanWorkingTree {
    $status = git status --porcelain
    if ($LASTEXITCODE -ne 0) {
        Fail-And-Pause "git status に失敗しました。"
    }

    if ($status) {
        Write-Host ""
        Write-Host "未コミットの変更があります。先にコミットまたは退避してください。" -ForegroundColor Yellow
        Write-Host "git status --short:"
        git status --short
        Fail-And-Pause "作業ツリーがクリーンではないため中止しました。"
    }
}

function Git-SafePull {
    Write-Host "Fetching latest changes..."
    git fetch origin main
    if ($LASTEXITCODE -ne 0) {
        Fail-And-Pause "git fetch origin main に失敗しました。"
    }

    Write-Host "Pulling with rebase..."
    git pull --rebase --autostash origin main
    if ($LASTEXITCODE -ne 0) {
        Fail-And-Pause "git pull --rebase --autostash origin main に失敗しました。OneDrive同期を一時停止し、VS CodeやExplorerを閉じて再実行してください。"
    }
}

# --- Git repo check ---
Ensure-GitRepo

# --- stale rebase cleanup ---
Remove-StaleRebaseState

# --- working tree check ---
Ensure-CleanWorkingTree

# --- always pull first ---
Git-SafePull

# Open file dialog
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = "Select Excel file to upload"
$dialog.Filter = "Excel files (*.xlsx)|*.xlsx"
$dialog.Multiselect = $false

$result = $dialog.ShowDialog()

if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
    Write-Host "Canceled."
    Pause
    exit 0
}

$sourceFile = $dialog.FileName

if (!(Test-Path $sourceFile)) {
    Fail-And-Pause "選択したファイルが見つかりません。"
}

Copy-Item -Path $sourceFile -Destination $targetFile -Force
Write-Host "Copied to incoming/forms_latest.xlsx"

git add incoming/forms_latest.xlsx
if ($LASTEXITCODE -ne 0) {
    Fail-And-Pause "git add に失敗しました。"
}

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "変更がないため、commit は不要です。"
    Pause
    exit 0
}

git commit -m "Update forms_latest.xlsx ($timestamp)"
if ($LASTEXITCODE -ne 0) {
    Fail-And-Pause "git commit に失敗しました。"
}

git push origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "push が拒否されたため、最新を再取得して再試行します..." -ForegroundColor Yellow

    Git-SafePull

    git push origin main
    if ($LASTEXITCODE -ne 0) {
        Fail-And-Pause "git push に失敗しました。競合やロックが残っている可能性があります。"
    }
}

Write-Host ""
Write-Host "Push completed. GitHub Actions should start automatically." -ForegroundColor Green
Pause