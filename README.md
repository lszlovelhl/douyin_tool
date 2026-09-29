# 抖音数据监控工具

![CI](https://github.com/lszlovelhl/douyin_tool/actions/workflows/test.yml/badge.svg)

基于 **Playwright + FastAPI + APScheduler + SQLite** 的抖音多账号数据监控系统。登录态持久化，定时采集粉丝 / 获赞 / 视频数据，点赞增量超阈值自动告警，飞书表格自动同步。

## 功能特性

### 核心监控
- **多账号并行扫描**：线程池并发（上限 3 防风控），每账号独立爬虫实例，账号级失败自动重试
- **定时扫描**：支持固定间隔（分钟级）和每天指定时间点两种模式
- **数据采集**：昵称、粉丝数、总获赞、视频列表（标题、点赞、发布时间、类型）
- **点赞增量计算**：相邻两次扫描增量、与首次扫描累计增量
- **阈值告警**：相邻增量超过设定阈值自动触发告警（默认 300）
- **新视频检测**：发现新发布视频自动告警
- **扫描锁**：防止上一次扫描未完成时重复启动

### 评论智能分析
- **评论采集**：自动展开评论列表，采集评论内容与作者
- **关键词识别**：自定义关键词列表，命中后生成拟回复
- **评论管理**：独立页面集中管理，筛选 / 编辑 / 一键复制 / 标记已回复

### 飞书集成
- **飞书表格 upsert**：`video_id → 行号` 映射，已存在更新原行、新视频追加；纯数字 ID 正则过滤脏数据
- **飞书机器人推送**：告警信息推送到飞书群
- **Token 自动刷新**：tenant_access_token 自动缓存与刷新

### 用户体验
- **自动登录**：未登录时弹窗引导，扫码后自动提取 cookies 并保存
- **Web 仪表盘**：概览卡片、增量排行、ECharts 趋势图、评论管理、暗色模式、CSV 导出、扫描进度条
- **一键启动**：macOS `.app` / Windows `.bat` + 桌面快捷方式

## 工程化亮点

- **SQLite 持久化**：WAL 并发安全，`scan_history` / `alerts` 双表带索引；首次运行自动从旧 JSON 迁移（真实迁移 419 条历史 + 143 条告警），旧文件保留为备份
- **启动自检**：`selfcheck.py` 全链路检查（依赖 / 抖音 cookies / 飞书配置 / webhook / 数据目录可写 / SQLite），启动时自动执行，`/api/status` 返回摘要
- **Docker + CI**：Dockerfile / docker-compose / GitHub Actions（38 项单元测试全绿）

## 快速开始

```bash
git clone https://github.com/lszlovelhl/douyin_tool.git
cd douyin_tool

# 1. 安装依赖
pip install -r requirements.txt
playwright install chromium        # 已装 Chrome 可跳过

# 2. 配置（复制模板，填写飞书密钥 / 账号 sec_uid）
cp config.example.json data/monitor_config.json

# 3. 启动，浏览器自动打开 http://localhost:8888
python app.py
```

首次使用点击页面右上角「登录」，扫码后自动提取 cookies 并保存；在「定时监控」页粘贴主页链接（支持 `www.douyin.com/user/...`、`v.douyin.com/...` 短链接），配置扫描模式与阈值后启动。

> 详细安装与使用见 [使用说明书.md](使用说明书.md)

## 项目结构

```
douyin_tool/
├── app.py                  # FastAPI 后端：API 路由 + APScheduler 调度 + 启动自检
├── crawler.py              # Playwright 爬虫：用户信息/视频/评论采集
├── monitor.py              # 监控核心：并行扫描/增量计算/告警/飞书同步
├── storage.py              # SQLite 持久化：scan_history / alerts（WAL，JSON 自动迁移）
├── selfcheck.py            # 启动自检：依赖/登录态/配置/数据目录全链路检查
├── login_manager.py        # 自动登录管理：有头浏览器扫码 + cookies 提取
├── lark_client.py          # 飞书电子表格客户端：upsert + Token 自动刷新
├── static/index.html       # 前端单页应用（仪表盘/采集/监控/评论管理）
├── tests/                  # 38 项单元测试（SQLite/并行/upsert/自检）
├── Dockerfile              # 容器化部署（python:3.11-slim + playwright chromium）
├── docker-compose.yml      # 端口 8888，data/ + cookies/ 数据卷持久化
├── .github/workflows/test.yml  # CI：py3.11 + playwright + unittest
├── config.example.json     # 配置模板（脱敏，复制到 data/ 填写）
├── cookies/                # 抖音登录态（首次登录后自动生成，不入库）
└── data/                   # 运行时数据（配置/历史/告警，自动生成，不入库）
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/status` | 系统状态（含自检摘要） |
| POST | `/api/crawl` | 单次采集 |
| POST | `/api/monitor/start` | 启动定时监控 |
| POST | `/api/monitor/scan-now` | 立即扫描一次 |
| GET | `/api/monitor/history` | 扫描历史 |
| GET | `/api/alerts` | 告警列表 |
| GET | `/api/comments` | 评论关键词命中列表 |
| POST | `/api/login/start` | 启动自动登录 |
| POST | `/api/lark/test` | 测试飞书连接 |

## 测试

```bash
python -m unittest discover tests -v   # 38 项全部通过
```

覆盖：SQLite 迁移与幂等、500 条历史上限、告警去重、已读流程、并发写入（5 线程 × 20 条）、飞书 upsert（ID 正则 / 索引 / 追加 / 更新 / 脏数据跳过）。

## Docker

```bash
docker compose up -d   # 端口 8888，data/ 与 cookies/ 数据卷持久化
```

## 技术说明

- **Playwright 渲染**：不依赖 a_bogus 签名，浏览器自动处理所有请求与风控
- **登录态复用**：cookies 文件持久化登录状态，过期可重新扫码
- **线程池隔离**：Playwright 同步 API 在 `run_in_threadpool` 中运行，不阻塞 FastAPI
- **并发安全**：SQLite WAL + 每读写事务串行锁；扫描进度更新加锁
- **防御性数据**：飞书 upsert 只接受 `^\d{6,}$` 纯数字 video_id，脏数据直接跳过

## 注意事项

- cookies 会过期，采集失败请重新扫码登录
- 频繁采集可能触发风控，建议扫描间隔不低于 5 分钟
- 本工具仅供学习研究使用
