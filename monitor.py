"""
监控核心逻辑
==============
- 定时扫描执行
- 点赞增量计算（相邻两次 / 与首次对比）
- 阈值告警触发
- 评论关键词识别 + 拟回复生成
"""
import time
import json
import os
import requests
import storage
from crawler import DouyinCrawler
from lark_client import LarkClient

# 绝对路径，避免后台调度器工作目录不对导致找不到 cookies
COOKIES_FILE = os.path.join(os.path.dirname(__file__), "cookies", "douyin_cookies.json")

# 扫描锁：防止上一次扫描还没完成时，下一次定时任务又启动
# True=正在扫描中，False=空闲
_scan_lock = False
_scan_start_time = None  # 当前扫描开始时间，用于诊断


def push_lark_webhook(config: dict, title: str, content: str):
    """通过飞书机器人 webhook 推送告警消息（失败自动重试，指数退避）"""
    webhook = config.get("lark_webhook", "")
    if not webhook:
        return
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "red"
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": content}}
            ]
        }
    }
    # 指数退避重试：最多 3 次（0s / 2s / 4s），容忍飞书临时限流或网络抖动
    for attempt in range(3):
        try:
            resp = requests.post(webhook, json=payload, timeout=10)
            if resp.status_code == 200:
                print(f"[飞书推送] 告警已推送: {title}")
                return
            print(f"[飞书推送] 推送失败: {resp.status_code}（第{attempt+1}次）")
        except Exception as e:
            print(f"[飞书推送] 推送异常: {e}（第{attempt+1}次）")
        if attempt < 2:
            time.sleep(2 ** attempt)
    print(f"[飞书推送] 重试 3 次仍失败，放弃推送: {title}")


# ==================== 评论拟回复模板 ====================
import re

REPLY_TEMPLATES = {
    "好听": "感谢喜欢！这首歌是《{song}》，歌手是{artist}，可以去音乐平台搜索收听哦～",
    "什么歌": "你好！这首歌是《{song}》，歌手是{artist}，希望你喜欢～",
    "求歌名": "来啦！这首歌是《{song}》，歌手是{artist}，记得收藏哦～",
    "BGM": "BGM是《{song}》，歌手是{artist}，喜欢的话可以去音乐平台搜索～",
    "bgm": "BGM是《{song}》，歌手是{artist}，喜欢的话可以去音乐平台搜索～",
    "歌名": "这首歌是《{song}》，歌手是{artist}，感谢喜欢～",
    "什么歌曲": "你好！这首歌是《{song}》，歌手是{artist}，希望你喜欢～",
    "这是什么歌": "这首歌是《{song}》，歌手是{artist}，感谢喜欢～",
}

DEFAULT_REPLY = "感谢你的评论！这首歌是《{song}》，歌手是{artist}，希望你喜欢～"

# 从评论中提取歌名的正则
SONG_PATTERNS = [
    r'[《<「]([^》>」]+)[》>」]',  # 《歌名》或 <歌名> 或 「歌名」
    r'[“”]([^“”]+)[“”]',          # “歌名” 或 “歌名”（修复：原字符类误用半角引号，中文引号歌名匹配不上）
    r'叫\s*([^\s，。！？]+)',       # 叫XXX
    r'是\s*([^\s，。！？]+)',       # 是XXX
]


def extract_song_name(text: str) -> str:
    """从评论中提取歌名，提取不到返回'未知'"""
    for pattern in SONG_PATTERNS:
        m = re.search(pattern, text)
        if m:
            song = m.group(1).strip()
            if len(song) <= 30:  # 合理长度
                return song
    return "未知"


def analyze_comments(comments: list, keywords: list) -> list:
    """
    识别评论中的关键词，生成拟回复。
    返回: [{text, author, matched_keyword, suggested_reply, song_name}]
    """
    results = []
    for c in comments:
        text = c.get("text", "")
        matched = None
        for kw in keywords:
            if kw in text:
                matched = kw
                break
        if matched:
            song_name = extract_song_name(text)
            template = REPLY_TEMPLATES.get(matched, DEFAULT_REPLY)
            # 如果评论中没提到歌名，用视频标题作为候选
            suggested_reply = template.format(song=song_name, artist="视频作者")
            results.append({
                "text": text,
                "author": c.get("author", ""),
                "matched_keyword": matched,
                "song_name": song_name,
                "suggested_reply": suggested_reply,
            })
    return results


# ==================== 单次扫描 ====================

def compute_deltas(videos: list, last_video_likes: dict, threshold: int) -> list:
    """
    计算每个视频的相邻点赞增量（纯函数，可单元测试）。

    :param videos: 本次扫描的视频列表（每项含 aweme_id、digg_count）
    :param last_video_likes: 上一次扫描的 {aweme_id: digg_count}，不含本次数据
    :param threshold: 告警阈值（用于标记 is_new 视频的初始点赞数）
    :return: 带增量字段的视频列表 [{..., last_likes, delta_from_last, is_new}]
    """
    result = []
    for v in videos:
        aweme_id = v["aweme_id"]
        current_likes = v.get("digg_count", 0)
        last_likes = last_video_likes.get(aweme_id)
        if last_likes is None:
            # 上一次扫描中没有这个视频，说明是新视频
            is_new = True
            delta = current_likes  # 新视频的增量就是当前点赞数
        else:
            is_new = False
            delta = current_likes - last_likes
        result.append({
            **v,
            "last_likes": last_likes if last_likes is not None else 0,
            "delta_from_last": delta,
            "is_new": is_new,
        })
    return result

def run_single_scan(crawler: DouyinCrawler, sec_uid: str, config: dict) -> dict:
    """
    执行单次扫描，计算增量，触发告警。
    返回扫描结果摘要。
    """
    # 采集数据
    user_info = crawler.get_user_info(sec_uid)
    videos = crawler.get_user_videos(
        sec_uid,
        max_count=config.get("max_videos", 20),
        scroll_times=config.get("scroll_times", 5),
        min_days=config.get("min_days", 0),
    )

    # 采集评论（如果启用）
    comment_alerts = []
    if config.get("get_comments", True):
        comment_check_count = config.get("comment_check_count", 2)  # 只检查前N条视频的评论
        for v in videos[:comment_check_count]:
            comments = crawler.get_video_comments(
                v["aweme_id"],
                max_count=config.get("max_comments", 20),
                video_type=v.get("type", "video"),
            )
            analyzed = analyze_comments(comments, config.get("comment_keywords", []))
            for item in analyzed:
                alert = storage.add_alert(
                    alert_type="comment_keyword",
                    sec_uid=sec_uid,
                    nickname=user_info.get("nickname", ""),
                    message=f"视频《{v.get('title', '')[:20]}》评论命中关键词「{item['matched_keyword']}」",
                    detail={
                        "video_id": v["aweme_id"],
                        "video_title": v.get("title", ""),
                        "comment_author": item["author"],
                        "comment_text": item["text"],
                        "matched_keyword": item["matched_keyword"],
                        "suggested_reply": item["suggested_reply"],
                    },
                    dedup_key=f"comment:{sec_uid}:{v['aweme_id']}:{item['author']}:{item['matched_keyword']}",
                )
                comment_alerts.append(alert)
                # 飞书 webhook 推送
                push_lark_webhook(
                    config,
                    title="💬 抖音评论关键词提醒",
                    content=f"**{user_info.get('nickname', '')}**\n"
                            f"视频：《{v.get('title', '')[:30]}》\n"
                            f"评论人：{item['author']}\n"
                            f"评论：{item['text'][:50]}\n"
                            f"命中关键词：**{item['matched_keyword']}**\n"
                            f"拟回复：{item['suggested_reply']}"
                )

    # 计算总点赞（仅作为参考指标，不作为告警依据）
    total_likes = sum(v.get("digg_count", 0) for v in videos)

    # 保存扫描记录
    record = storage.add_scan_record(sec_uid, user_info.get("nickname", ""), videos, total_likes)

    # 获取上一次扫描中各视频的点赞数（批量查询，用于计算单个视频的相邻增量）
    last_video_likes = storage.get_all_video_last_likes(sec_uid)
    # 注意：刚保存的 record 会被 get_last_scan 返回，所以需要排除本次记录
    # get_all_video_last_likes 内部调用 get_last_scan，会返回刚保存的本次数据
    # 所以这里需要手动找倒数第二次扫描
    history = storage.get_history(sec_uid, limit=10)
    if len(history) >= 2:
        # history[0] 是本次（刚保存），history[1] 是上一次
        last_scan_record = history[1]
        last_video_likes = {v["aweme_id"]: v.get("digg_count", 0) for v in last_scan_record.get("videos", [])}
    else:
        last_video_likes = {}  # 第一次扫描，没有上一次数据

    # 按单个视频计算相邻增量，超阈值告警
    threshold = config.get("like_threshold", 300)
    video_alerts = []
    videos_with_delta = compute_deltas(videos, last_video_likes, threshold)

    for v in videos_with_delta:
        aweme_id = v["aweme_id"]
        current_likes = v.get("digg_count", 0)
        last_likes = v.get("last_likes", 0)
        delta = v.get("delta_from_last", 0)
        is_new = v.get("is_new", False)

        # 单个视频相邻增量超过阈值 → 告警（同视频 1 小时内去重，避免连续扫描反复轰炸）
        if not is_new and delta >= threshold:
            alert = storage.add_alert(
                alert_type="video_like_threshold",
                sec_uid=sec_uid,
                nickname=user_info.get("nickname", ""),
                message=f"视频《{v.get('title', '')[:25]}》点赞增量 +{delta}，超过阈值 {threshold}",
                detail={
                    "video_id": aweme_id,
                    "video_title": v.get("title", ""),
                    "current_likes": current_likes,
                    "last_likes": last_likes,
                    "delta_from_last": delta,
                    "threshold": threshold,
                },
                dedup_key=f"like:{sec_uid}:{aweme_id}",
            )
            video_alerts.append(alert)
            # 飞书 webhook 推送
            push_lark_webhook(
                config,
                title="⚠️ 抖音视频点赞增量告警",
                content=f"**{user_info.get('nickname', '')}**\n"
                        f"视频：《{v.get('title', '')[:40]}》\n"
                        f"当前点赞：{current_likes}\n"
                        f"上次点赞：{last_likes}\n"
                        f"相邻增量：**+{delta}**\n"
                        f"告警阈值：{threshold}"
            )

    # 新视频检测（单独告警）
    new_videos = [v for v in videos_with_delta if v["is_new"]]
    if new_videos and len(history) >= 2:
        storage.add_alert(
            alert_type="new_video",
            sec_uid=sec_uid,
            nickname=user_info.get("nickname", ""),
            message=f"发现 {len(new_videos)} 条新内容",
            detail={
                "new_videos": [
                    {"aweme_id": v["aweme_id"], "title": v.get("title", "")[:30], "digg_count": v.get("digg_count", 0)}
                    for v in new_videos
                ],
            },
            dedup_key=f"new:{sec_uid}",
        )

    # 飞书表格同步（按视频维度写入，每个视频一行）
    if config.get("lark_enabled") and config.get("lark_app_id") and config.get("lark_spreadsheet_token"):
        try:
            lark = LarkClient(
                app_id=config["lark_app_id"],
                app_secret=config["lark_app_secret"],
                spreadsheet_token=config["lark_spreadsheet_token"],
                sheet_id=config.get("lark_sheet_id", "0"),
            )
            for v in videos_with_delta:
                lark.append_video_record(
                    nickname=user_info.get("nickname", ""),
                    video_title=v.get("title", ""),
                    video_id=v["aweme_id"],
                    current_likes=v.get("digg_count", 0),
                    last_likes=v.get("last_likes", 0),
                    delta=v.get("delta_from_last", 0),
                    is_new=v.get("is_new", False),
                )
        except Exception as e:
            print(f"[飞书] 同步失败: {e}")

    return {
        "user": user_info,
        "videos": videos_with_delta,
        "total_likes": total_likes,
        "video_alerts": len(video_alerts),
        "comment_alerts": len(comment_alerts),
        "record": record,
    }


# ==================== 定时任务入口 ====================

def scheduled_scan(force: bool = False):
    """定时任务回调：扫描所有配置的账号"""
    global _scan_lock, _scan_start_time

    # 检查锁：如果上一次扫描还在进行，跳过本次
    if _scan_lock:
        duration = time.time() - _scan_start_time if _scan_start_time else 0
        print(f"[定时扫描] 上一次扫描仍在进行中（已运行{duration:.0f}秒），跳过本次")
        return

    config = storage.get_config()
    if not force and not config.get("enabled", False):
        return

    sec_uids = config.get("sec_uids", [])
    if not sec_uids:
        return

    # 加锁
    _scan_lock = True
    _scan_start_time = time.time()

    try:
        _do_scan(sec_uids, config)
    finally:
        # 无论成功失败，都释放锁
        _scan_lock = False
        _scan_start_time = None


def _do_scan(sec_uids: list, config: dict):
    """实际执行扫描的内部函数（多账号并行：每账号独立浏览器实例，最多 3 路并发）"""
    import threading
    import concurrent.futures

    print(f"[定时扫描] 开始扫描 {len(sec_uids)} 个账号，时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # 从历史记录中预取账号昵称映射，让进度显示更直观
    nickname_map = {}
    all_history = storage.get_history(limit=200)
    for h in all_history:
        uid = h.get("sec_uid", "")
        if uid and uid not in nickname_map and h.get("nickname"):
            nickname_map[uid] = h["nickname"]

    # 记录扫描进度
    scan_progress = {
        "total": len(sec_uids),
        "current": 0,
        "current_nickname": "",
        "results": [],
        "errors": [],
        "start_time": time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    storage.set_scan_progress(scan_progress)
    progress_lock = threading.Lock()

    def _update_progress(**kwargs):
        """线程安全地更新扫描进度"""
        with progress_lock:
            scan_progress.update(kwargs)
            storage.set_scan_progress(scan_progress)

    def _scan_one(index: int, sec_uid: str):
        """单个账号的完整扫描流程（独立浏览器实例，线程内独享）"""
        crawler = DouyinCrawler(cookies_file=COOKIES_FILE, headless=True)
        try:
            crawler.start()
        except Exception as e:
            print(f"[定时扫描] 浏览器启动失败: {e}")
            _update_progress(
                errors=scan_progress["errors"] + [f"账号{sec_uid[:20]}...浏览器启动失败: {e}"]
            )
            return None

        try:
            for attempt in range(2):  # 每个账号最多重试1次
                try:
                    result = run_single_scan(crawler, sec_uid, config)
                    nickname = result['user'].get('nickname', '')
                    total_delta = sum(v.get("delta_from_last", 0) for v in result['videos'] if not v.get("is_new"))
                    alert_count = result.get('video_alerts', 0)
                    _update_progress(
                        current=index + 1,
                        current_nickname=nickname,
                        results=scan_progress["results"] + [{
                            "sec_uid": sec_uid,
                            "nickname": nickname,
                            "total_likes": result['total_likes'],
                            "total_delta": total_delta,
                            "video_alerts": alert_count,
                            "video_count": len(result['videos']),
                        }],
                    )
                    print(f"[定时扫描] [{index+1}/{len(sec_uids)}] {nickname}: "
                          f"总点赞 {result['total_likes']}, "
                          f"视频总增量 +{total_delta}, "
                          f"触发告警 {alert_count} 条")
                    return result
                except Exception as e:
                    print(f"[定时扫描] [{index+1}/{len(sec_uids)}] 账号 {sec_uid[:20]}... 第{attempt+1}次尝试失败: {e}")
                    if attempt == 0:
                        # 第一次失败，尝试重启浏览器
                        try:
                            crawler.close()
                            time.sleep(2)
                            crawler = DouyinCrawler(cookies_file=COOKIES_FILE, headless=True)
                            crawler.start()
                            print(f"[定时扫描] 浏览器已重启，准备重试")
                        except Exception as e2:
                            print(f"[定时扫描] 浏览器重启失败: {e2}")
                    else:
                        _update_progress(
                            errors=scan_progress["errors"] + [f"账号{sec_uid[:20]}...: {e}"]
                        )
                        return None
        finally:
            try:
                crawler.close()
            except Exception:
                pass
        return None

    max_workers = min(len(sec_uids), 3)  # 并发上限 3，避免触发平台风控
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_scan_one, i, uid) for i, uid in enumerate(sec_uids)]
        results = [f.result() for f in futures]

    scan_progress["end_time"] = time.strftime('%Y-%m-%d %H:%M:%S')
    storage.set_scan_progress(scan_progress)
    success = sum(1 for r in results if r is not None)
    print(f"[定时扫描] 完成，成功 {success}/{len(sec_uids)} 个账号，时间: {scan_progress['end_time']}")
