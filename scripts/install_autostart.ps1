# Tu chay bot moi khi dang nhap Windows: tao shortcut trong thu muc Startup (khong can quyen admin).
#   powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1            # cai
#   powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1 -Remove    # go
# MT5: bot goi mt5.initialize(), terminal se tu mo neu chua chay (dat MT5_PATH trong .env neu may co nhieu MT5).
param([switch]$Remove)
$Root = Split-Path -Parent $PSScriptRoot
$Startup = [Environment]::GetFolderPath("Startup")
$Lnk = Join-Path $Startup "trading-bot-telegram.lnk"

if ($Remove) {
    if (Test-Path $Lnk) { Remove-Item $Lnk -Confirm:$false; Write-Host "Da go: $Lnk" } else { Write-Host "Chua cai." }
    exit 0
}

$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($Lnk)
$s.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$s.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$Root\scripts\run_bot.ps1`""
$s.WorkingDirectory = $Root
$s.WindowStyle = 7    # thu nho
$s.Description = "Telegram + MT5 trading bot"
$s.Save()
Write-Host "Da cai: $Lnk"
Write-Host "Bot se chay khi dang nhap Windows. Chay ngay: powershell -ExecutionPolicy Bypass -File `"$Root\scripts\run_bot.ps1`""
Write-Host "Luu y: tat Sleep cua may (Settings > System > Power) neu muon bot chay ca dem."
