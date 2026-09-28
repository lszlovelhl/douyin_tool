@echo off
chcp 65001 >nul
title 抖音数据监控 - 创建桌面快捷方式

echo ========================================
echo   抖音数据监控 - 创建桌面快捷方式
echo ========================================
echo.

set SCRIPT_DIR=%~dp0
set VBS_PATH=%SCRIPT_DIR%启动抖音监控.vbs
set DESKTOP=%USERPROFILE%\Desktop
set SHORTCUT=%DESKTOP%\抖音数据监控.lnk

echo [*] 正在创建桌面快捷方式...

powershell -Command "$ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut('%SHORTCUT%'); $sc.TargetPath = '%VBS_PATH%'; $sc.WorkingDirectory = '%SCRIPT_DIR%'; $sc.Description = '抖音数据监控一键启动'; $sc.Save()"

if exist "%SHORTCUT%" (
    echo [√] 桌面快捷方式创建成功！
    echo [√] 位置: %SHORTCUT%
) else (
    echo [×] 创建失败，请手动创建快捷方式
)

echo.
echo 双击桌面的"抖音数据监控"即可启动！
echo.
pause
