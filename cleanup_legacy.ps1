# Removes the leftover OrchestrAI / old LawPilot files that the rebuilt app no longer uses.
#
# A full backup of the original project was saved to:
#   %USERPROFILE%\Downloads\LawPilot-original-backup-2026-10-13.zip
#
# Usage (from the project folder):
#   powershell -ExecutionPolicy Bypass -File .\cleanup_legacy.ps1          # lists files, asks before deleting
#   powershell -ExecutionPolicy Bypass -File .\cleanup_legacy.ps1 -DryRun  # only lists

param([switch]$DryRun)

$root = $PSScriptRoot

$keepRoot = @(
    '.github', '.gitattributes', '.gitignore', '.env', '.env.example', '.claude', '.venv', 'venv', 'instance',
    'app.py', 'requirements.txt', 'requirements-dev.txt', 'Procfile', 'pytest.ini', 'README.md', 'cleanup_legacy.ps1',
    'Dockerfile', 'docker-compose.yml', '.vercelignore',
    'lawpilot', 'static', 'tests'
)
$keepPackage = @(
    '__init__.py', 'activity.py', 'auth.py', 'chunking.py', 'config.py', 'db.py', 'draft_templates.py',
    'embeddings.py', 'extract.py', 'guard.py', 'ingestion.py', 'llm.py', 'mailer.py', 'query_analysis.py',
    'rbac.py', 'reminders.py', 'reranking.py', 'research.py', 'retrieval.py', 'seed.py', 'settings.py',
    'importers.py', 'indiacode.py', 'data', 'routes'
)

$targets = @()
$targets += Get-ChildItem -LiteralPath $root -Force | Where-Object { $keepRoot -notcontains $_.Name }
$targets += Get-ChildItem -LiteralPath (Join-Path $root 'lawpilot') -Force | Where-Object { $keepPackage -notcontains $_.Name }
$targets += Get-ChildItem -LiteralPath (Join-Path $root 'tests') -Force |
    Where-Object { $_.Name -ne 'conftest.py' -and $_.Name -notlike 'test_app_*.py' }

if ($targets.Count -eq 0) {
    Write-Host 'Nothing to clean up.'
    exit 0
}

Write-Host "The following $($targets.Count) items will be deleted:`n"
$targets | ForEach-Object { Write-Host ('  ' + $_.FullName.Substring($root.Length + 1)) }

if ($DryRun) { exit 0 }

$answer = Read-Host "`nType DELETE to remove them"
if ($answer -ne 'DELETE') {
    Write-Host 'Cancelled. Nothing was deleted.'
    exit 1
}

$targets | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }
Write-Host "Removed $($targets.Count) items."
