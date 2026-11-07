# PDX Beacon Studio - pacchetto Windows (PyInstaller, cartella unica con ffmpeg dentro) e zip per la release.
# Uso: powershell -ExecutionPolicy Bypass -File build_exe.ps1 [-FFmpegDir <cartella con bin\ffmpeg.exe>]
param([string]$FFmpegDir = "")
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$tools = Join-Path $root "tools"
$py = Join-Path $tools ".venv\Scripts\python.exe"
$ver = (Select-String -Path (Join-Path $tools "studio\pdx_studio.py") -Pattern '^APP_VERSION = "(.+)"').Matches[0].Groups[1].Value

if (-not $FFmpegDir) {
    $ff = (Get-Command ffmpeg -ErrorAction Stop).Source
    $FFmpegDir = Split-Path (Split-Path $ff)
}
$ffbin = Join-Path $FFmpegDir "bin"
foreach ($f in "ffmpeg.exe", "ffprobe.exe") {
    if (-not (Test-Path (Join-Path $ffbin $f))) { throw "manca $f in $ffbin" }
}
Write-Host "Versione $ver, ffmpeg da $ffbin"

Push-Location $tools
try {
    & $py -m PyInstaller --noconfirm --clean --windowed --onedir --name PDX_Beacon_Studio `
        --distpath (Join-Path $root "dist") --workpath (Join-Path $root "build") --specpath (Join-Path $root "build") `
        --paths $tools --paths (Join-Path $tools "dvbs2") `
        --hidden-import make_es --hidden-import make_bg --hidden-import make_symbols --hidden-import encoder `
        --add-data "$(Join-Path $tools 'dvbs2\ldpc_tables.json');." `
        --add-data "$(Join-Path $ffbin 'ffmpeg.exe');ffmpeg" `
        --add-data "$(Join-Path $ffbin 'ffprobe.exe');ffmpeg" `
        --add-data "$(Join-Path $FFmpegDir 'LICENSE');ffmpeg" `
        --add-data "$(Join-Path $FFmpegDir 'README.txt');ffmpeg" `
        --exclude-module tkinter `
        (Join-Path $tools "studio\pdx_studio.py")
    if ($LASTEXITCODE) { throw "PyInstaller: errore $LASTEXITCODE" }
} finally { Pop-Location }

$app = Join-Path $root "dist\PDX_Beacon_Studio"
foreach ($f in "LICENSE", "README.md", "README_it.md", "THIRD_PARTY.md", "CHANGELOG.md") {
    Copy-Item (Join-Path $root $f) $app
}
$rel = Join-Path $root "release"
New-Item -ItemType Directory -Force $rel | Out-Null
$zip = Join-Path $rel "PDX_Beacon_Studio_v${ver}_Windows.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $app -DestinationPath $zip -CompressionLevel Optimal
$h = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
"$h  $(Split-Path $zip -Leaf)" | Set-Content (Join-Path $rel "SHA256SUMS.txt") -Encoding ascii
Write-Host "Pacchetto: $zip ($([math]::Round((Get-Item $zip).Length / 1MB, 1)) MB)"
Write-Host "SHA-256:   $h"
