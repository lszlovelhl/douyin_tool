#!/bin/bash
# 抖音数据监控系统 - 一键安装脚本 (macOS/Linux)
echo "========================================"
echo "  抖音数据监控系统 - 一键安装"
echo "========================================"
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 未找到 python3，请先安装 Python 3.8+"
    exit 1
fi
echo "✅ Python 版本: $(python3 --version)"

# 进入项目目录
cd "$(dirname "$0")"

# 安装 Python 依赖
echo ""
echo "📦 安装 Python 依赖..."
pip3 install -r requirements.txt
if [ $? -ne 0 ]; then
    echo "❌ 依赖安装失败"
    exit 1
fi
echo "✅ Python 依赖安装完成"

# 安装 Playwright 浏览器
echo ""
echo "🌐 安装 Playwright 浏览器..."
python3 -m playwright install chromium
if [ $? -ne 0 ]; then
    echo "⚠️  Playwright 浏览器安装失败"
    echo "   如已安装 Google Chrome，可跳过此步，程序会自动使用系统 Chrome"
else
    echo "✅ Playwright 浏览器安装完成"
fi

# 创建必要目录
mkdir -p cookies data

echo ""
echo "========================================"
echo "  ✅ 安装完成！"
echo "========================================"
echo ""
echo "启动方式："
echo "  方式1: 双击 start.sh"
echo "  方式2: 终端执行 python3 app.py"
echo ""
echo "启动后浏览器自动打开 http://localhost:8888"
echo "首次使用请点击页面右上角「登录」扫码登录抖音"
echo ""
read -p "按回车键退出..."
