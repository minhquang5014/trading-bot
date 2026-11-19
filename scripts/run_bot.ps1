# Chay bot Telegram + MT5 va tu khoi dong lai neu bot dung (mat mang, MT5 tat, loi bat ngo).
#   powershell -ExecutionPolicy Bypass -File scripts\run_bot.ps1
# Log: live\logs\bot_YYYYMMDD.log. Dung han: dong cua so nay (hoac Ctrl+C hai lan).
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Bot = Join-Path $Root "telegram_bot\bot.py"
$LogDir = Join-Path $Root "live\logs"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
Set-Location $Root

if (-not (Test-Path $Python)) {
    Write-Host "Khong thay $Python. Tao venv truoc: python -m venv .venv; .venv\Scripts\python -m pip install -r requirements.txt"
    exit 1
}

$delay = 30
while ($true) {
    $log = Join-Path $LogDir ("bot_{0}.log" -f (Get-Date -Format "yyyyMMdd"))
    $start = Get-Date
    "==== $(Get-Date -Format s) start bot ====" | Out-File -Append -Encoding utf8 $log
    & $Python $Bot *>&1 | ForEach-Object { $_.ToString() } | Tee-Object -Append -FilePath $log
    $code = $LASTEXITCODE
    "==== $(Get-Date -Format s) bot exited (code $code), restart in $delay s ====" | Out-File -Append -Encoding utf8 $log
    # chay duoc lau thi reset thoi gian cho; loi lien tuc (sai .env, MT5 chua mo) thi cho lau dan, toi da 10 phut
    if (((Get-Date) - $start).TotalMinutes -gt 10) { $delay = 30 } else { $delay = [Math]::Min($delay * 2, 600) }
    Start-Sleep -Seconds $delay
}
