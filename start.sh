#!/bin/bash
# 抖音数据监控 - 一键启动脚本
# 功能：启动后端服务 + 自动打开浏览器

PROJECT_DIR="/Users/zhuolittlelong/Projects/douyin_tool"
PORT=8888
URL="http://localhost:${PORT}"
LOG_FILE="/tmp/douyin_monitor_app.log"

cd "$PROJECT_DIR" || exit 1

# 检查服务是否已在运行
check_service() {
    curl -s -o /dev/null -w "%{http_code}" "http://localhost:${PORT}/api/status" 2>/dev/null
}

# 如果服务已运行，直接打开浏览器
if [ "$(check_service)" = "200" ]; then
    echo "服务已在运行，直接打开浏览器..."
    open "$URL"
    exit 0
fi

# 启动后端服务（后台运行）
echo "正在启动抖音数据监控服务..."
echo "日志文件: $LOG_FILE"
nohup python3 app.py > "$LOG_FILE" 2>&1 &
SERVER_PID=$!

# 等待服务启动（最多等30秒）
echo "等待服务启动..."
for i in $(seq 1 30); do
    sleep 1
    if [ "$(check_service)" = "200" ]; then
        echo "服务启动成功！"
        sleep 1
        open "$URL"
        exit 0
    fi
    echo "  等待中... (${i}s)"
done

echo "服务启动超时，请检查日志: $LOG_FILE"
exit 1
