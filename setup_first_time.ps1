[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Windows.Forms

function Fail-And-Pause {
    param([string]$Message)
    Write-Host ""
    Write-Host "ERROR: $Message" -ForegroundColor Red
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

function Info {
    param([string]$Message)
    Write-Host $Message -ForegroundColor Cyan
}

function Success {
    param([string]$Message)
    Write-Host $Message -ForegroundColor Green
}

function Warn {
    param([string]$Message)
    Write-Host $Message -ForegroundColor Yellow
}

Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " matching-automatic First-Time Setup Tool" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

# -----------------------------
# Settings
# -----------------------------
$RepoUrl = "https://github.com/T-Kawaguchi-lab/matching-automatic.git"
$ExpectedRemotePattern = "T-Kawaguchi-lab/matching-automatic"
$DefaultFolderName = "matching_automatic"

# -----------------------------
# Check Git
# -----------------------------
Info "Checking Git installation..."
git --version *> $null
if ($LASTEXITCODE -ne 0) {
    Fail-And-Pause "Git is not installed. Please install Git first."
}
Success "Git is installed."

# -----------------------------
# Choose parent folder
# -----------------------------
Info "Please choose a parent folder where the repository will be cloned."

$folderDialog = New-Object System.Windows.Forms.FolderBrowserDialog
$folderDialog.Description = "Select a folder to save the matching_automatic repository"
$folderDialog.ShowNewFolderButton = $true

$dialogResult = $folderDialog.ShowDialog()
if ($dialogResult -ne [System.Windows.Forms.DialogResult]::OK) {
    Fail-And-Pause "No folder selected."
}

$ParentFolder = $folderDialog.SelectedPath
if (-not (Test-Path $ParentFolder)) {
    Fail-And-Pause "Selected folder does not exist."
}

$RepoFolder = Join-Path $ParentFolder $DefaultFolderName

Write-Host ""
Info "Selected parent folder: $ParentFolder"
Info "Repository folder: $RepoFolder"
Write-Host ""

# -----------------------------
# If folder exists
# -----------------------------
if (Test-Path $RepoFolder) {
    Warn "The folder already exists: $RepoFolder"

    $existingGit = Join-Path $RepoFolder ".git"
    if (Test-Path $existingGit) {
        Info "Existing Git repository detected. Checking remote..."

        Push-Location $RepoFolder
        try {
            $remoteUrl = git remote get-url origin 2>$null
            if ($LASTEXITCODE -ne 0) {
                Fail-And-Pause "The existing folder is a Git repository, but origin remote is not configured."
            }

            if ($remoteUrl -notmatch [regex]::Escape($ExpectedRemotePattern)) {
                Fail-And-Pause "The existing repository is not matching-automatic. Remote URL: $remoteUrl"
            }

            Success "Existing matching-automatic repository found."
            Warn "Clone step will be skipped."

            $cloneSkipped = $true
        }
        finally {
            Pop-Location
        }
    }
    else {
        Fail-And-Pause "The folder already exists but is not a Git repository. Please remove or rename it first."
    }
}
else {
    $cloneSkipped = $false
}

# -----------------------------
# Clone if needed
# -----------------------------
if (-not $cloneSkipped) {
    Info "Cloning repository..."
    git clone $RepoUrl $RepoFolder
    if ($LASTEXITCODE -ne 0) {
        Fail-And-Pause "git clone failed."
    }
    Success "Repository cloned successfully."
}

# -----------------------------
# Enter repository
# -----------------------------
Push-Location $RepoFolder
try {
    Write-Host ""
    Info "Verifying repository state..."

    # Check inside git work tree
    $insideRepo = git rev-parse --is-inside-work-tree 2>$null
    if ($LASTEXITCODE -ne 0 -or $insideRepo.Trim() -ne "true") {
        Fail-And-Pause "This folder is not a valid Git repository."
    }

    # Check remote
    $remoteUrl = git remote get-url origin 2>$null
    if ($LASTEXITCODE -ne 0) {
        Fail-And-Pause "origin remote is not configured."
    }
    if ($remoteUrl -notmatch [regex]::Escape($ExpectedRemotePattern)) {
        Fail-And-Pause "origin remote is not the expected repository. Found: $remoteUrl"
    }
    Success "Remote repository check passed."

    # Fetch latest
    Info "Fetching latest remote info..."
    git fetch origin main
    if ($LASTEXITCODE -ne 0) {
        Fail-And-Pause "Failed to fetch from origin."
    }
    Success "Fetch completed."

    # Ensure branch main
    $currentBranch = git branch --show-current 2>$null
    if ($LASTEXITCODE -ne 0) {
        Fail-And-Pause "Could not determine current branch."
    }

    if ($currentBranch.Trim() -ne "main") {
        Warn "Current branch is '$currentBranch'. Switching to 'main'..."
        git checkout main
        if ($LASTEXITCODE -ne 0) {
            Fail-And-Pause "Failed to switch to main branch."
        }
    }
    Success "Branch is main."

    # Pull latest
    Info "Pulling latest changes..."
    git pull --rebase --autostash origin main
    if ($LASTEXITCODE -ne 0) {
        Fail-And-Pause "git pull --rebase failed."
    }
    Success "Repository is up to date."

    # Check GitHub authentication by reading remote
    Info "Checking GitHub access..."
    git ls-remote origin *> $null
    if ($LASTEXITCODE -ne 0) {
        Fail-And-Pause "Cannot access GitHub remote. Please sign in to GitHub for this PC."
    }
    Success "GitHub remote access is available."

    # Check whether push permission likely works
    Write-Host ""
    Warn "This setup confirmed that the PC can access the repository."
    Warn "To push successfully, the signed-in GitHub account must have write access (collaborator)."

    # Show next step
    Write-Host ""
    Success "First-time setup completed successfully."
    Write-Host ""
    Write-Host "Repository location:" -ForegroundColor Cyan
    Write-Host $RepoFolder -ForegroundColor White
    Write-Host ""
    Write-Host "Next step:" -ForegroundColor Cyan
    Write-Host "Open the repository folder and run upload_excel.bat" -ForegroundColor White

    # Open folder
    Write-Host ""
    $openFolder = Read-Host "Open the repository folder now? (Y/N)"
    if ($openFolder.Trim().ToUpper() -eq "Y") {
        Start-Process explorer.exe $RepoFolder
    }
}
finally {
    Pop-Location
}

Write-Host ""
Read-Host "Press Enter to finish"