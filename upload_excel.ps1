Add-Type -AssemblyName System.Windows.Forms

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

$incomingDir = Join-Path $repoRoot "incoming"
$targetFile = Join-Path $incomingDir "forms_latest.xlsx"

if (!(Test-Path $incomingDir)) {
    New-Item -ItemType Directory -Path $incomingDir | Out-Null
}

# ファイル選択ダイアログ
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = "アップロードするExcelを選択してください"
$dialog.Filter = "Excel files (*.xlsx)|*.xlsx"
$dialog.Multiselect = $false

$result = $dialog.ShowDialog()

if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
    Write-Host "キャンセルされました。"
    exit 0
}

$sourceFile = $dialog.FileName

if (!(Test-Path $sourceFile)) {
    Write-Host "選択したファイルが見つかりません。"
    exit 1
}

Copy-Item -Path $sourceFile -Destination $targetFile -Force
Write-Host "コピー完了: $targetFile"

# git 状態確認
git rev-parse --is-inside-work-tree *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "このフォルダは Git リポジトリではありません。"
    exit 1
}

git pull origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "git pull に失敗しました。競合や認証を確認してください。"
    exit 1
}

git add incoming/forms_latest.xlsx

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
git commit -m "Update forms_latest.xlsx ($timestamp)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "コミット対象がありません。ファイル内容が同じ可能性があります。"
    exit 0
}

git push origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "git push に失敗しました。認証または競合を確認してください。"
    exit 1
}

Write-Host "push 完了。GitHub Actions が自動実行されます。"
Pause