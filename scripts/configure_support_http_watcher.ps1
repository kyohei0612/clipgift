# v4 サポート: HTTP poller のタスクスケジューラ登録スクリプト
#
# 旧 IMAP 系（"ClipGift Support Mail Watcher" + idle_watcher）を停止・解除し、
# 新 HTTP poller（"ClipGift Support HTTP Watcher"）を「ログオン時起動」で常駐登録する。
#
# 使い方:
#   管理者 PowerShell で実行
#   PS> cd C:\Users\kyohei\ClipGift
#   PS> .\scripts\configure_support_http_watcher.ps1
#
# 削除したい場合:
#   PS> Unregister-ScheduledTask -TaskName 'ClipGift Support HTTP Watcher' -Confirm:$false

param(
    [string]$NewTaskName = 'ClipGift Support HTTP Watcher',
    [string]$OldTaskName = 'ClipGift Support Mail Watcher'
)

$ErrorActionPreference = 'Stop'

# プロジェクトルート（このスクリプトの 1 階層上）
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

$WatchScript = Join-Path $ProjectRoot 'scripts\watch_support_http.py'
if (-not (Test-Path $WatchScript)) {
    Write-Error "watch_support_http.py が見つかりません: $WatchScript"
    exit 1
}

# Python 実行ファイルの解決（pythonw.exe = GUI 用、コンソールウィンドウなし）
$VenvPythonw = Join-Path $ProjectRoot '.venv\Scripts\pythonw.exe'
if (Test-Path $VenvPythonw) {
    $PythonExe = $VenvPythonw
} else {
    $PythonCmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -eq $PythonCmd) {
        Write-Error 'Python が見つかりません。PATH に python.exe が無いか、.venv が無いです。'
        exit 1
    }
    $PythonwExe = $PythonCmd.Source -replace 'python\.exe$', 'pythonw.exe'
    if (-not (Test-Path $PythonwExe)) {
        Write-Warning "pythonw.exe が見つかりません、python.exe で代替します（コンソール窓が出ます）"
        $PythonExe = $PythonCmd.Source
    } else {
        $PythonExe = $PythonwExe
    }
}

Write-Host "Python:   $PythonExe"
Write-Host "Watcher:  $WatchScript"
Write-Host ""

# ────────────────────────────────────────────────────────────
# Step 1: 旧 IMAP 系タスクの解除
# ────────────────────────────────────────────────────────────
$old = Get-ScheduledTask -TaskName $OldTaskName -ErrorAction SilentlyContinue
if ($old) {
    Write-Host "旧タスクを解除: $OldTaskName"
    Stop-ScheduledTask -TaskName $OldTaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $OldTaskName -Confirm:$false
} else {
    Write-Host "旧タスク $OldTaskName は登録されていません（スキップ）"
}

# 旧 idle_watcher プロセスがあれば停止
$idleProcs = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like '*watch_support_idle.py*' -or
    $_.CommandLine -like '*watch_support_mail.py*'
}
foreach ($p in $idleProcs) {
    Write-Host "旧 watcher プロセス停止: PID=$($p.ProcessId) cmd=$($p.CommandLine.Substring(0, [Math]::Min(80, $p.CommandLine.Length)))..."
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}

# ────────────────────────────────────────────────────────────
# Step 2: 新 HTTP watcher タスク登録
# ────────────────────────────────────────────────────────────
$existing = Get-ScheduledTask -TaskName $NewTaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "既存の同名タスクを削除: $NewTaskName"
    Stop-ScheduledTask -TaskName $NewTaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $NewTaskName -Confirm:$false
}

# Action: pythonw.exe で watch_support_http.py を実行（常駐）
$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$WatchScript`"" `
    -WorkingDirectory $ProjectRoot

# Trigger:
#   1. ユーザーログオン時に起動（常駐ループなので一度起動すれば走り続ける）
#   2. PC 起動時にも起動（Boot Trigger / 自動ログインが入らないコールドスタート保険）
$LogonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$BootTrigger  = New-ScheduledTaskTrigger -AtStartup
$Trigger = @($LogonTrigger, $BootTrigger)

# Settings: ネット必須、バッテリー駆動でも実行、5 分後に再起動
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Days 7)

# Principal: ログオンユーザー権限で実行
#   LogonType S4U（Service for User）: ユーザーがログオンしていなくても実行可能。
#   pythonw.exe は GUI 表示なしの常駐プロセスなのでログオン非依存で問題ない。
#   これにより Boot Trigger（AtStartup）がログオン前でも発火するようになり、
#   「自動ログインが入らないコールドスタート保険」というコメント目的を達成する。
#   Interactive のままだと StartWhenAvailable=True でログオン後に発火するため、
#   実質 AtLogOn と同じ挙動になっていた。
$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType S4U `
    -RunLevel Limited

# 登録
Register-ScheduledTask `
    -TaskName $NewTaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description 'ClipGift サポートセンター v4 - Cloudflare Email Worker からの HTTP ポーリング常駐 watcher'

Write-Host ''
Write-Host "[OK] 新タスク登録完了: $NewTaskName"
Write-Host ''
Write-Host '即時起動して動作確認:'
Write-Host "  Start-ScheduledTask -TaskName '$NewTaskName'"
Write-Host '  ログ: support_center\incoming\_http_watcher.log'
Write-Host ''
Write-Host '次回 Windows ログオン時から自動的に起動する。'
