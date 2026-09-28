"""
抖音数据采集工具 - FastAPI 后端
================================
功能：
- 单次采集（用户信息 + 视频列表 + 评论）
- 定时监控（间隔可配、增量计算、阈值告警、评论关键词识别拟回复）
- 历史记录与告警查询
"""
import os
import json
import time
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler

from crawler import DouyinCrawler
import storage
import monitor
import login_manager
import selfcheck

app = FastAPI(title="抖音数据采集工具", version="2.1")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

COOKIES_FILE = os.path.join(os.path.dirname(__file__), "cookies", "douyin_cookies.json")
PORT = int(os.environ.get("PORT", "8888"))

# 启动自检：缺失依赖/凭据/登录态时，服务启动日志直接给出修复提示
_STARTUP_REPORT = selfcheck.run_checks()
selfcheck.print_report(_STARTUP_REPORT)

# 定时调度器
scheduler = BackgroundScheduler(timezone="Asia/Shanghai")


def _run_crawl(sec_uids: list, max_videos: int, get_comments: bool,
               max_comments: int, scroll_times: int, min_days: int = 0) -> list:
    """单次采集（线程池运行）"""
    crawler = DouyinCrawler(cookies_file=COOKIES_FILE, headless=True)
    try:
        crawler.start()
        all_results = []
        for sec_uid in sec_uids:
            try:
                user_info = crawler.get_user_info(sec_uid)
                videos = crawler.get_user_videos(
                    sec_uid, max_count=max_videos, scroll_times=scroll_times,
                    min_days=min_days
                )
                if get_comments:
                    for v in videos:
                        v['comments'] = crawler.get_video_comments(
                            v['aweme_id'], max_count=max_comments,
                            video_type=v.get('type', 'video')
                        )
                total_likes = sum(v.get('digg_count', 0) for v in videos)
                all_results.append({
                    "user": user_info, "videos": videos,
                    "video_count": len(videos), "total_likes": total_likes,
                })
            except Exception as e:
                all_results.append({"sec_uid": sec_uid, "error": str(e), "user": None, "videos": []})
        return all_results
    finally:
        crawler.close()


# ==================== 请求模型 ====================

class CrawlRequest(BaseModel):
    sec_uids: list[str]
    max_videos: int = 20
    min_days: int = 0
    get_comments: bool = False
    max_comments: int = 20
    scroll_times: int = 5


class MonitorConfig(BaseModel):
    interval_minutes: int = 30
    schedule_mode: str = "interval"  # interval / fixed
    schedule_times: list[str] = []
    cron_expression: str = ""
    like_threshold: int = 300
    sec_uids: list[str] = []
    max_videos: int = 20
    min_days: int = 0
    scroll_times: int = 5
    get_comments: bool = True
    max_comments: int = 20
    comment_check_count: int = 2
    comment_keywords: list[str] = []
    enabled: bool = False
    lark_enabled: bool = False
    lark_app_id: str = ""
    lark_app_secret: str = ""
    lark_spreadsheet_token: str = ""
    lark_sheet_id: str = "0"
    lark_webhook: str = ""


# ==================== 页面路由 ====================

@app.get("/", response_class=HTMLResponse)
async def index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>抖音数据采集工具</h1>"


# ==================== 登录状态检测 ====================

@app.get("/api/auth/check")
async def check_login():
    """检测 cookies 是否有效（是否登录），同时提取用户资料"""
    def _check():
        crawler = DouyinCrawler(cookies_file=COOKIES_FILE, headless=True)
        try:
            crawler.start()
            result = crawler.check_login_status()
            # 登录成功则保存用户资料
            if result.get("loggedIn") and (result.get("nickname") or result.get("avatar")):
                storage.save_user_profile(
                    nickname=result.get("nickname", ""),
                    avatar=result.get("avatar", "")
                )
            return result
        finally:
            crawler.close()
    result = await run_in_threadpool(_check)
    return result


@app.get("/api/auth/profile")
async def get_profile():
    """获取当前登录用户的资料（昵称、头像）"""
    return storage.get_user_profile()


@app.post("/api/auth/start-login")
async def start_login():
    """启动有头浏览器，引导用户扫码登录，成功后自动保存 cookies"""
    result = login_manager.start_login(COOKIES_FILE)
    return result


@app.get("/api/auth/login-status")
async def login_status():
    """查询扫码登录状态（pending/saving/success/failed/cancelled）"""
    return login_manager.get_login_status()


@app.post("/api/auth/cancel-login")
async def cancel_login():
    """取消登录流程"""
    return login_manager.cancel_login()


# ==================== 单次采集 API ====================

@app.get("/api/status")
async def status():
    cookies_exist = os.path.exists(COOKIES_FILE)
    cookies_count = 0
    if cookies_exist:
        with open(COOKIES_FILE, "r") as f:
            cookies_count = len(json.load(f))
    config = storage.get_config()
    jobs = scheduler.get_jobs()
    return {
        "status": "running",
        "cookies_loaded": cookies_exist,
        "cookies_count": cookies_count,
        "monitor_enabled": config.get("enabled", False),
        "monitor_running": len(jobs) > 0,
        "next_run_time": jobs[0].next_run_time.strftime("%Y-%m-%d %H:%M:%S") if jobs else None,
        # 启动自检摘要（页面可据此展示环境就绪状态）
        "selfcheck": {
            "ok": _STARTUP_REPORT["ok"],
            "checks": _STARTUP_REPORT["checks"],
            "fix_hints": _STARTUP_REPORT["fix_hints"],
        },
    }


@app.post("/api/crawl")
async def crawl(req: CrawlRequest):
    if not req.sec_uids:
        raise HTTPException(status_code=400, detail="sec_uids 不能为空")
    # 自动从主页链接中提取 sec_uid
    cleaned_uids = []
    for s in req.sec_uids:
        uid = DouyinCrawler.extract_sec_uid(s)
        if uid:
            cleaned_uids.append(uid)
        else:
            raise HTTPException(status_code=400, detail=f"无法从输入中提取 sec_uid: {s[:30]}...")
    start_time = time.time()
    results = await run_in_threadpool(
        _run_crawl, cleaned_uids, req.max_videos, req.get_comments,
        req.max_comments, req.scroll_times, req.min_days
    )
    elapsed = round(time.time() - start_time, 2)
    return {"success": True, "elapsed_seconds": elapsed, "accounts": len(results), "results": results}


# ==================== 监控配置 API ====================

@app.get("/api/monitor/config")
async def get_monitor_config():
    return storage.get_config()


@app.get("/api/monitor/status")
async def get_monitor_status():
    """获取监控运行状态"""
    job = scheduler.get_job("douyin_monitor")
    config = storage.get_config()
    return {
        "running": job is not None,
        "next_run_time": job.next_run_time.strftime("%Y-%m-%d %H:%M:%S") if job and job.next_run_time else None,
        "enabled": config.get("enabled", False),
        "interval_minutes": config.get("interval_minutes", 30),
        "cron_expression": config.get("cron_expression", ""),
    }


@app.post("/api/monitor/config")
async def update_monitor_config(config: MonitorConfig):
    # 自动从主页链接中提取 sec_uid
    cleaned_uids = []
    for s in config.sec_uids:
        uid = DouyinCrawler.extract_sec_uid(s)
        if uid:
            cleaned_uids.append(uid)
    config.sec_uids = cleaned_uids
    saved = storage.save_config(config.model_dump())
    if saved.get("enabled"):
        _restart_scheduler()
    else:
        _stop_scheduler()
    return {"success": True, "config": saved}


@app.post("/api/lark/test")
async def test_lark_connection():
    """测试飞书表格连接"""
    config = storage.get_config()
    if not config.get("lark_app_id") or not config.get("lark_spreadsheet_token"):
        return {"success": False, "message": "请先填写飞书 app_id 和表格 token"}
    from lark_client import LarkClient
    lark = LarkClient(
        app_id=config["lark_app_id"],
        app_secret=config.get("lark_app_secret", ""),
        spreadsheet_token=config["lark_spreadsheet_token"],
        sheet_id=config.get("lark_sheet_id", "0"),
    )
    result = lark.test_connection()
    return result


@app.post("/api/monitor/start")
async def start_monitor(config: MonitorConfig):
    # 自动从主页链接中提取 sec_uid
    cleaned_uids = []
    for s in config.sec_uids:
        uid = DouyinCrawler.extract_sec_uid(s)
        if uid:
            cleaned_uids.append(uid)
    config.sec_uids = cleaned_uids
    if not config.sec_uids:
        raise HTTPException(status_code=400, detail="无法从输入中提取 sec_uid，请检查链接格式")
    config.enabled = True
    storage.save_config(config.model_dump())
    # 启动前校验登录状态
    def _check():
        crawler = DouyinCrawler(cookies_file=COOKIES_FILE, headless=True)
        try:
            crawler.start()
            return crawler.check_login_status()
        finally:
            crawler.close()
    login_status = await run_in_threadpool(_check)
    if not login_status.get("loggedIn"):
        raise HTTPException(status_code=403, detail="未检测到有效登录态，请先更新 cookies。抖音未登录时无法查看完整个人主页，监控无法正常工作。")
    _restart_scheduler()
    return {"success": True, "message": "监控已启动", "interval_minutes": config.interval_minutes}


@app.post("/api/monitor/stop")
async def stop_monitor():
    config = storage.get_config()
    config["enabled"] = False
    storage.save_config(config)
    _stop_scheduler()
    return {"success": True, "message": "监控已停止"}


@app.post("/api/monitor/scan-now")
async def scan_now():
    """立即执行一次扫描（忽略 enabled 状态）"""
    config = storage.get_config()
    if not config.get("sec_uids"):
        raise HTTPException(status_code=400, detail="请先配置监控账号")
    await run_in_threadpool(monitor.scheduled_scan, True)
    return {"success": True, "message": "扫描完成"}


# ==================== 历史与告警 API ====================

@app.get("/api/monitor/history")
async def get_history(sec_uid: str = None, limit: int = 50):
    return storage.get_history(sec_uid=sec_uid, limit=limit)


@app.get("/api/monitor/video-history")
async def get_video_history(sec_uid: str = None, limit: int = 200):
    """视频维度的扫描历史（扁平化，每个视频一行）"""
    return storage.get_video_history(sec_uid=sec_uid, limit=limit)


@app.get("/api/monitor/video-trend")
async def get_video_trend(aweme_id: str, limit: int = 50):
    """单个视频的点赞趋势"""
    return storage.get_video_trend(aweme_id, limit=limit)


@app.get("/api/monitor/hot-videos")
async def get_hot_videos(limit: int = 10):
    """热门视频排行榜（按连续增长趋势排序）"""
    return storage.get_hot_videos(limit=limit)


@app.get("/api/monitor/video-trend/{aweme_id}")
async def get_video_trend(aweme_id: str):
    """获取单个视频的点赞增长历史"""
    return storage.get_video_trend(aweme_id)


@app.get("/api/monitor/alerts")
async def get_alerts(unread_only: bool = False, limit: int = 100):
    return storage.get_alerts(unread_only=unread_only, limit=limit)


@app.post("/api/monitor/alerts/{alert_id}/read")
async def mark_alert_read(alert_id: int):
    storage.mark_alert_read(alert_id)
    return {"success": True}


@app.post("/api/monitor/alerts/read-all")
async def mark_all_alerts_read():
    storage.mark_all_alerts_read()
    return {"success": True}


@app.post("/api/monitor/alerts/clear")
async def clear_alerts():
    storage.clear_alerts()
    return {"success": True}


# ==================== 扫描进度 ====================

@app.get("/api/monitor/scan-progress")
async def get_scan_progress():
    return storage.get_scan_progress()


@app.get("/api/monitor/scan-status")
async def get_scan_status():
    """查询当前扫描状态（是否正在扫描、已运行多久）"""
    return {
        "scanning": monitor._scan_lock,
        "running_seconds": int(time.time() - monitor._scan_start_time) if monitor._scan_start_time else 0,
    }


# ==================== 账号备注 ====================

@app.get("/api/monitor/account-notes")
async def get_account_notes():
    return storage.get_account_notes()


@app.post("/api/monitor/account-notes")
async def save_account_note(req: dict):
    storage.save_account_note(req.get("sec_uid", ""), req.get("note", ""))
    return {"success": True}


# ==================== 数据导出 ====================

@app.get("/api/monitor/export")
async def export_history(sec_uid: str = None):
    """导出扫描历史为CSV"""
    import csv
    import io
    from fastapi.responses import StreamingResponse

    history = storage.get_history(sec_uid=sec_uid, limit=500)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["扫描时间", "抖音昵称", "sec_uid", "视频数", "总点赞", "视频标题", "视频点赞"])
    for h in history:
        for v in h.get("videos", []):
            writer.writerow([
                h.get("datetime", ""),
                h.get("nickname", ""),
                h.get("sec_uid", "")[:20],
                h.get("video_count", 0),
                h.get("total_likes", 0),
                v.get("title", ""),
                v.get("digg_count", 0),
            ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=douyin_history.csv"}
    )


# ==================== 评论管理 ====================

@app.get("/api/comments")
async def get_comments(status: str = "all"):
    """获取评论关键词命中列表"""
    return storage.get_comments(status=status)


@app.post("/api/comments/{comment_id}/reply")
async def mark_comment_replied(comment_id: int, req: dict = None):
    """标记评论已回复"""
    custom_reply = req.get("custom_reply") if req else None
    storage.mark_comment_replied(comment_id, custom_reply)
    return {"success": True}


@app.post("/api/comments/{comment_id}/unreply")
async def mark_comment_unreplied(comment_id: int):
    """标记评论未回复"""
    storage.mark_comment_unreplied(comment_id)
    return {"success": True}


@app.post("/api/comments/{comment_id}/reply-text")
async def update_comment_reply(comment_id: int, req: dict):
    """更新拟回复内容"""
    storage.update_comment_reply(comment_id, req.get("reply_text", ""))
    return {"success": True}


# ==================== 调度器管理 ====================

def _restart_scheduler():
    """重启定时任务，支持间隔模式和指定时间模式"""
    _stop_scheduler()
    config = storage.get_config()
    if not config.get("enabled", False):
        return

    mode = config.get("schedule_mode", "interval")

    if mode == "fixed":
        # 每天指定时间点模式：将 ["09:00", "12:00", "18:00"] 转为 cron "0 9,12,18 * * *"
        times = config.get("schedule_times", [])
        if times:
            hours = []
            minutes = []
            for t in times:
                parts = t.split(":")
                if len(parts) == 2:
                    hours.append(str(int(parts[0])))
                    minutes.append(str(int(parts[1])))
            if hours:
                from apscheduler.triggers.cron import CronTrigger
                hour_str = ",".join(sorted(set(hours), key=int))
                minute_str = ",".join(sorted(set(minutes), key=int)) if len(set(minutes)) > 1 else minutes[0]
                trigger = CronTrigger(
                    minute=minute_str, hour=hour_str,
                    timezone="Asia/Shanghai"
                )
                scheduler.add_job(
                    monitor.scheduled_scan,
                    trigger,
                    id="douyin_monitor",
                    replace_existing=True,
                )
                print(f"[调度器] 已启动，每天指定时间: {', '.join(times)}")
                return

    # 默认使用间隔分钟
    interval = max(1, config.get("interval_minutes", 30))
    from datetime import datetime, timedelta
    scheduler.add_job(
        monitor.scheduled_scan,
        "interval",
        minutes=interval,
        id="douyin_monitor",
        replace_existing=True,
        next_run_time=datetime.now() + timedelta(seconds=5),  # 5秒后执行第一次
    )
    print(f"[调度器] 已启动，间隔 {interval} 分钟")


def _stop_scheduler():
    """停止定时任务"""
    job = scheduler.get_job("douyin_monitor")
    if job:
        scheduler.remove_job("douyin_monitor")
        print("[调度器] 已停止")


@app.on_event("startup")
async def startup_event():
    scheduler.start()
    config = storage.get_config()
    if config.get("enabled", False) and config.get("sec_uids"):
        _restart_scheduler()
    print("[启动] 调度器已初始化")


@app.on_event("shutdown")
async def shutdown_event():
    scheduler.shutdown(wait=False)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
