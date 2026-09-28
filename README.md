# 抖音数据监控系统

基于 Playwright + FastAPI + APScheduler 的抖音账号数据监控工具，携带登录态访问，自动处理风控，支持定时扫描、点赞增量告警、飞书表格同步、评论关键词识别与拟回复。

## 功能特性

### 核心监控
- **多账号同时监控**：支持批量粘贴主页链接，自动提取 sec_uid
- **定时扫描**：支持固定间隔（分钟级）和每天指定时间点（时:分）两种模式
- **数据采集**：昵称、粉丝数、总获赞、视频列表（标题、点赞、发布时间、类型）
- **点赞增量计算**：相邻两次扫描增量、与首次扫描累计增量
- **阈值告警**：相邻增量超过设定阈值自动触发告警（默认300）
- **新视频检测**：发现新发布视频自动告警
- **扫描锁**：防止上一次扫描未完成时重复启动

### 评论智能分析
- **评论采集**：自动展开评论列表，采集评论内容与作者
- **关键词识别**：可自定义关键词列表（默认：好听、什么歌、求歌名、BGM、歌名、这是什么歌）
- **拟回复生成**：命中关键词后自动生成拟回复模板，支持从评论中提取歌名
- **评论管理**：独立页面集中管理，支持筛选（待回复/已回复）、编辑拟回复、一键复制、标记已回复

### 飞书集成
- **飞书表格同步**：每次扫描后自动将数据写入飞书电子表格
- **飞书机器人推送**：告警信息通过自定义机器人推送到飞书群
- **Token 自动刷新**：tenant_access_token 自动缓存与刷新

### 用户体验
- **自动登录**：未登录时弹窗引导，扫码后自动提取 cookies 并保存
- **用户资料同步**：登录后显示抖音头像、昵称
- **Web 前端**：仪表盘、单次采集、定时监控、评论管理四个 Tab
- **暗色模式**：支持浅色/暗色主题切换，自动保存偏好
- **ECharts 趋势图**：点赞数趋势可视化
- **扫描进度条**：实时显示当前扫描账号、成功/失败数
- **自动刷新**：监控历史自动轮询更新
- **配置自动保存**：修改配置后2秒防抖自动保存
- **数据导出**：历史记录导出为 CSV

### 一键启动
- **macOS**：双击 `.app` 即可启动，自动打开浏览器
- **Windows**：双击 `.bat` 或 `.vbs` 启动，支持创建桌面快捷方式

## 快速开始

### 1. 安装依赖

```bash
cd douyin_tool
pip install -r requirements.txt
playwright install chromium
```

> macOS 如已安装 Google Chrome，可跳过 `playwright install`，代码会自动使用系统 Chrome。

### 2. 启动服务

```bash
python app.py
```

浏览器自动打开 `http://localhost:8888`

### 3. 登录抖音

首次使用点击页面右上角「登录」，浏览器自动打开抖音扫码页面，扫码登录后自动提取 cookies 并返回监控页面。

### 4. 添加监控账号

在「定时监控」页面粘贴抖音主页链接（支持 `www.douyin.com/user/...`、`v.douyin.com/...` 短链接等格式），系统自动提取 sec_uid。

### 5. 配置并启动

- 设置扫描模式（固定间隔 / 每天指定时间）
- 设置告警阈值（默认相邻增量 > 300 告警）
- 配置飞书表格和机器人（可选）
- 点击「启动监控」

## 项目结构

```
douyin_tool/
├── app.py                  # FastAPI 后端，API 路由 + APScheduler 调度
├── crawler.py              # Playwright 爬虫，用户信息/视频/评论采集
├── monitor.py              # 监控核心，扫描执行/增量计算/告警/飞书同步
├── storage.py              # JSON 持久化，配置/历史/告警/进度存储
├── login_manager.py        # 自动登录管理，有头浏览器扫码 + cookies 提取
├── lark_client.py          # 飞书电子表格客户端，Token 自动刷新
├── static/
│   └── index.html          # 前端单页应用（仪表盘/采集/监控/评论管理）
├── cookies/
│   └── douyin_cookies.json # 抖音登录态（首次登录后自动生成）
├── data/                   # 运行时数据（配置/历史/告警，自动生成）
├── start.sh                # macOS 启动脚本
├── start.bat               # Windows 启动脚本
├── 启动抖音监控.vbs         # Windows 隐藏控制台启动
├── 创建桌面快捷方式.bat     # Windows 桌面快捷方式创建
├── requirements.txt
└── README.md
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 前端页面 |
| GET | `/api/status` | 系统状态 |
| POST | `/api/crawl` | 单次采集 |
| GET | `/api/monitor/config` | 获取监控配置 |
| POST | `/api/monitor/config` | 保存监控配置 |
| POST | `/api/monitor/start` | 启动定时监控 |
| POST | `/api/monitor/stop` | 停止定时监控 |
| POST | `/api/monitor/scan-now` | 立即扫描一次 |
| GET | `/api/monitor/history` | 扫描历史 |
| GET | `/api/monitor/scan-progress` | 扫描进度 |
| GET | `/api/monitor/scan-status` | 扫描锁状态 |
| GET | `/api/alerts` | 告警列表 |
| POST | `/api/alerts/{id}/read` | 标记告警已读 |
| GET | `/api/comments` | 评论关键词命中列表 |
| POST | `/api/comments/{id}/reply` | 标记评论已回复 |
| POST | `/api/login/start` | 启动自动登录 |
| GET | `/api/login/status` | 登录状态 |
| POST | `/api/lark/test` | 测试飞书连接 |

## 技术说明

- **Playwright 渲染**：不依赖 a_bogus 签名，浏览器自动处理所有请求与风控
- **登录态复用**：cookies 文件持久化登录状态，过期可重新扫码
- **线程池隔离**：Playwright 同步 API 在 `run_in_threadpool` 中运行，不阻塞 FastAPI
- **APScheduler 调度**：支持间隔触发和日期触发，自动管理任务生命周期
- **扫描互斥锁**：全局锁防止并发扫描，保证数据一致性
- **飞书 OpenAPI**：tenant_access_token 自动缓存刷新，表格追加写入

## 注意事项

- cookies 会过期，如采集失败请重新扫码登录
- 频繁采集可能触发风控，建议扫描间隔不低于 5 分钟
- 评论采集会显著增加扫描时间，建议每个账号检查 2 条视频
- 本工具仅供学习研究使用
