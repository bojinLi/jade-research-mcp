param([string]$Python = '3.12')
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$env:UV_CACHE_DIR = Join-Path $projectRoot '.uv-cache'
$env:TMP = Join-Path $projectRoot '.tmp'
$env:TEMP = $env:TMP
New-Item -ItemType Directory -Path $env:TMP -Force | Out-Null
Push-Location $projectRoot
try {
    uv venv --python $Python .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed' }
    uv pip install --python '.venv\Scripts\python.exe' -r requirements.lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
    Write-Output 'Installed. See README.md to connect the local MCP server.'
} finally { Pop-Location }
