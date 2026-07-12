# ClipGift Support HTTP Watcher に「1時間ごとの自己回復トリガー」を追加する。
# セッション中にプロセスが落ちても、最大1時間で自動復活させる保険。
# 管理者権限が必要 → 未昇格なら自己昇格して再実行する。

$ErrorActionPreference = 'Stop'
$name = 'ClipGift Support HTTP Watcher'

# --- 自己昇格 ---
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
  ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $isAdmin) {
    Write-Host '管理者権限で再実行します（UAC を承認してください）...'
    Start-Process pwsh.exe -Verb RunAs -ArgumentList @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File', $PSCommandPath
    )
    exit
}

$task = Get-ScheduledTask -TaskName $name
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
            -RepetitionInterval (New-TimeSpan -Hours 1) `
            -RepetitionDuration (New-TimeSpan -Days 3650)
$triggers = @($task.Triggers) + $repeat
$settings = $task.Settings
$settings.MultipleInstances = 'IgnoreNew'

Set-ScheduledTask -TaskName $name -Trigger $triggers -Settings $settings | Out-Null

Write-Host ''
Write-Host '[OK] 1時間ごとの自己回復トリガーを追加しました。'
Write-Host '更新後トリガー:'
(Get-ScheduledTask -TaskName $name).Triggers | ForEach-Object {
    $rep = if ($_.Repetition.Interval) { $_.Repetition.Interval } else { '(なし)' }
    Write-Host ('  - ' + $_.CimClass.CimClassName + '  repeat=' + $rep)
}
Write-Host ('MultipleInstances = ' + (Get-ScheduledTask -TaskName $name).Settings.MultipleInstances)
Write-Host ''
Write-Host 'このウィンドウは Enter で閉じられます。'
Read-Host
