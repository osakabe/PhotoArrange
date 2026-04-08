@echo off
setlocal
chcp 65001 >nul

echo Antigravity IDE 拡張機能をインストールしています...

:: パスの設定
set "SRC_DIR=.\PhotoArrange Lead Architect"
set "DEST_DIR=%USERPROFILE%\.antigravity\extensions\photoarrange-architect-agent"

:: ターゲットディレクトリの作成
if not exist "%DEST_DIR%" (
    mkdir "%DEST_DIR%"
    if errorlevel 1 (
        echo [エラー] インストール先ディレクトリの作成に失敗しました。
        pause
        exit /b 1
    )
)

:: ファイルのコピー
copy /Y "%SRC_DIR%\extension.toml" "%DEST_DIR%\" >nul
if errorlevel 1 (
    echo [エラー] extension.toml のコピーに失敗しました。
    pause
    exit /b 1
)

copy /Y "%SRC_DIR%\GEMINI.md" "%DEST_DIR%\" >nul
if errorlevel 1 (
    echo [エラー] GEMINI.md のコピーに失敗しました。
    pause
    exit /b 1
)

echo.
echo [成功] インストールが完了しました！
echo インストール先: %DEST_DIR%
echo.
pause