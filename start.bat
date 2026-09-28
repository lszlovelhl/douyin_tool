@echo off
chcp 65001 >nul
title 抖音数据监控

REM 切换到脚本所在目录
cd /d "%~dp0"

set PORT=8888
set URL=http://localhost:%PORT%
set LOG_FILE=%TEMP%\douyin_monitor.log

echo ========================================
echo   抖音数据监控 - 启动中...
echo ========================================
echo.

REM 检查服务是否已在运行
powershell -Command "try { $r = Invoke-WebRequest -Uri '%URL%/api/status' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }"
if %errorlevel% equ 0 (
    echo [√] 服务已在运行，直接打开浏览器...
    start "" "%URL%"
    timeout /t 2 >nul
    exit 0
)

REM 启动后端服务
echo [*] 正在启动后端服务...
echo [*] 日志文件: %LOG_FILE%
start /b python app.py > "%LOG_FILE%" 2>&1

REM 等待服务启动（最多等30秒）
echo [*] 等待服务启动...
set /a count=0
:wait_loop
timeout /t 1 >nul
set /a count+=1
powershell -Command "try { $r = Invoke-WebRequest -Uri '%URL%/api/status' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }"
if %errorlevel% equ 0 (
    echo.
    echo [√] 服务启动成功！
    echo [√] 正在打开浏览器...
    timeout /t 1 >nul
    start "" "%URL%"
    exit 0
)
if %count% lss 30 goto wait_loop

echo.
echo [×] 服务启动超时！
echo [×] 请检查日志: %LOG_FILE%
echo.
pause
exit 1
