# BOOTH 出品用 ZIP パッケージング
#
# 使い方:
#   .\scripts\package_for_booth.ps1
#
# 出力:
#   installer_output\ClipGift_v<version>.zip
#
# 同梱物:
#   - ClipGift_Setup.exe (installer_output/)
#   - README.txt          (installer_assets/)
#   - LICENSE.txt         (installer_assets/)
#
# 前提:
#   - build_and_push.bat（または ISCC ビルド）で installer_output\ClipGift_Setup.exe が生成済
#   - installer_assets\README.txt / LICENSE.txt が準備済

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$SetupExe = Join-Path $Root 'installer_output\ClipGift_Setup.exe'
$Readme   = Join-Path $Root 'installer_assets\README.txt'
$License  = Join-Path $Root 'installer_assets\LICENSE.txt'
$VersionFile = Join-Path $Root 'version.json'

# 入力チェック
foreach ($f in @($SetupExe, $Readme, $License, $VersionFile)) {
    if (-not (Test-Path $f)) {
        Write-Error "✗ 必要ファイルが見つかりません: $f"
        exit 1
    }
}

# バージョン取得
$Version = (Get-Content $VersionFile -Raw | ConvertFrom-Json).version
$ZipPath = Join-Path $Root "installer_output\ClipGift_v$Version.zip"

Write-Host "=== ClipGift BOOTH パッケージング ==="
Write-Host "  バージョン: $Version"
Write-Host "  出力先: $ZipPath"
Write-Host ""

# 既存 ZIP があれば削除
if (Test-Path $ZipPath) {
    Write-Host "  既存 ZIP を削除: $ZipPath"
    Remove-Item $ZipPath -Force
}

# ZIP 作成
Write-Host "  同梱:"
Write-Host "    - $(Split-Path $SetupExe -Leaf) ($(($((Get-Item $SetupExe).Length) / 1MB).ToString('F2')) MB)"
Write-Host "    - $(Split-Path $Readme -Leaf)"
Write-Host "    - $(Split-Path $License -Leaf)"

Compress-Archive -Path $SetupExe, $Readme, $License -DestinationPath $ZipPath -CompressionLevel Optimal

# 結果
$ZipSizeMB = (Get-Item $ZipPath).Length / 1MB
Write-Host ""
Write-Host "✅ ZIP 作成完了"
Write-Host "  ファイル: $ZipPath"
Write-Host "  サイズ: $($ZipSizeMB.ToString('F2')) MB"

# BOOTH のファイル容量制限チェック（1 ファイル 1.2GB / 商品全体 10GB）
if ($ZipSizeMB -gt 1200) {
    Write-Warning "⚠ BOOTH 1 ファイル上限 (1.2GB = 1200MB) 超過: ${ZipSizeMB}MB"
} else {
    Write-Host "  BOOTH 上限: 1200 MB（余裕: $((1200 - $ZipSizeMB).ToString('F0')) MB）"
}

Write-Host ""
Write-Host "次のステップ: BOOTH 商品ページの「商品データ」にこの ZIP をアップロード"
