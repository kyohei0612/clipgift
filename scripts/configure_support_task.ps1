# Windows タスクスケジューラに「ClipGift Support Mail Watcher」を登録する PowerShell スクリプト
#
# 使い方:
#   管理者権限の PowerShell で実行
#   PS> cd C:\Users\kyohei\ClipGift
#   PS> .\scripts\configure_support_task.ps1
#
# 削除したい場合:
#   PS> Unregister-ScheduledTask -TaskName 'ClipGift Support Mail Watcher' -Confirm:$false

param(
    [int]$IntervalMinutes = 10,
    [string]$TaskName = 'ClipGift Support Mail Watcher'
)

$ErrorActionPreference = 'Stop'

# プロジェクトルート（このスクリプトの 1 階層上）
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

$WatchScript = Join-Path $ProjectRoot 'scripts\watch_support_mail.py'
if (-not (Test-Path $WatchScript)) {
    Write-Error "watch_support_mail.py が見つかりません: $WatchScript"
    exit 1
}

# Python 実行ファイルの解決（pythonw.exe = GUI 用、コンソールウィンドウなし）
# 仮想環境があれば優先、無ければシステム Python を探す
$VenvPythonw = Join-Path $ProjectRoot '.venv\Scripts\pythonw.exe'
if (Test-Path $VenvPythonw) {
    $PythonExe = $VenvPythonw
} else {
    $PythonCmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -eq $PythonCmd) {
        Write-Error 'Python が見つかりません。PATH に python.exe が無いか、.venv が無いです。'
        exit 1
    }
    # python.exe → pythonw.exe へ変換（同じディレクトリにあるはず）
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
Write-Host "Interval: $IntervalMinutes 分"
Write-Host ""

# 既存タスクがあれば削除（再登録のため）
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "既存タスクを削除します: $TaskName"
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Action: python.exe で watch_support_mail.py を実行
$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$WatchScript`"" `
    -WorkingDirectory $ProjectRoot

# Trigger: 登録 2 分後に最初の起動 → IntervalMinutes 分ごとに繰り返し（365 日継続）
$StartTime = (Get-Date).AddMinutes(2)
$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At $StartTime `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 365)

# Settings: バッテリー駆動でも実行 / ネット必須 / 実行時間 30 分上限
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

# Principal: ログオンユーザー権限で実行
$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

# 登録
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description 'ClipGift サポートセンターのメール監視（IMAP ポーリング + Claude CLI 起動）'

Write-Host ''
Write-Host "[OK] タスク登録完了: $TaskName"
Write-Host ''
Write-Host '動作確認:'
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host '  ログ: support_center\incoming\_watcher.log'
Write-Host ''
