@echo off
chcp 65001 >nul
echo ========================================
echo   抖音数据监控系统 - 一键安装
echo ========================================
echo.

:: 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 未找到 python，请先安装 Python 3.8+
    pause
    exit /b 1
)
echo ✅ Python 版本:
python --version

:: 进入项目目录
cd /d "%~dp0"

:: 安装 Python 依赖
echo.
echo 📦 安装 Python 依赖...
pip install -r requirements.txt
if errorlevel 1 (
    echo ❌ 依赖安装失败
    pause
    exit /b 1
)
echo ✅ Python 依赖安装完成

:: 安装 Playwright 浏览器
echo.
echo 🌐 安装 Playwright 浏览器...
python -m playwright install chromium
if errorlevel 1 (
    echo ⚠️  Playwright 浏览器安装失败
    echo    如已安装 Google Chrome，可跳过此步，程序会自动使用系统 Chrome
) else (
    echo ✅ Playwright 浏览器安装完成
)

:: 创建必要目录
if not exist cookies mkdir cookies
if not exist data mkdir data

echo.
echo ========================================
echo   ✅ 安装完成！
echo ========================================
echo.
echo 启动方式：
echo   方式1: 双击 start.bat
echo   方式2: 双击 启动抖音监控.vbs （隐藏控制台）
echo   方式3: 命令行执行 python app.py
echo.
echo 启动后浏览器自动打开 http://localhost:8888
echo 首次使用请点击页面右上角「登录」扫码登录抖音
echo.
pause
