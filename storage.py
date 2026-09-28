"""
数据持久化存储（SQLite 核心 + JSON 辅助）
========================================
扫描历史与告警存入 SQLite（WAL 模式、线程安全、自动建表），
配置 / 进度 / 备注 / 评论回复 / 用户资料等小体量数据仍用 JSON 文件。

首次使用时自动把旧 JSON 数据迁移进 SQLite（幂等：迁移只执行一次，
迁移完成后旧 JSON 文件保留为备份，不再参与读写）。

对外函数签名与旧版完全一致，app.py / monitor.py / 前端无感切换。
"""
import json
import os
import sqlite3
import threading
import time
from typing import Optional

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
HISTORY_FILE = os.path.join(DATA_DIR, "scan_history.json")
ALERTS_FILE = os.path.join(DATA_DIR, "alerts.json")
CONFIG_FILE = os.path.join(DATA_DIR, "monitor_config.json")
PROFILE_FILE = os.path.join(DATA_DIR, "user_profile.json")
PROGRESS_FILE = os.path.join(DATA_DIR, "scan_progress.json")
ACCOUNT_NOTES_FILE = os.path.join(DATA_DIR, "account_notes.json")
COMMENT_REPLIES_FILE = os.path.join(DATA_DIR, "comment_replies.json")
DB_FILE = os.path.join(DATA_DIR, "monitor.db")
MIGRATED_FLAG = os.path.join(DATA_DIR, ".sqlite_migrated")

HISTORY_KEEP = 500    # 扫描历史最多保留条数（与旧版一致）
ALERTS_KEEP = 200     # 告警最多保留条数（与旧版一致）

_conn = None
_conn_lock = threading.Lock()   # 保护每次读写的完整事务序列
_init_lock = threading.Lock()   # 仅保护连接初始化（避免与 _conn_lock 嵌套死锁）


def _ensure_dir():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)


def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return default


def _save_json(path: str, data):
    _ensure_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ==================== SQLite 基础 ====================

def _get_conn() -> sqlite3.Connection:
    """获取全局 SQLite 连接（双检锁懒初始化，首次使用时自动建表 + 迁移旧数据）。"""
    global _conn
    if _conn is None:
        with _init_lock:
            if _conn is None:
                _ensure_dir()
                _conn = sqlite3.connect(DB_FILE, check_same_thread=False)
                _conn.row_factory = sqlite3.Row
                _conn.execute("PRAGMA journal_mode=WAL")
                _conn.execute("PRAGMA synchronous=NORMAL")
                _conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS scan_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL NOT NULL,
                        datetime TEXT NOT NULL,
                        sec_uid TEXT NOT NULL,
                        nickname TEXT NOT NULL DEFAULT '',
                        total_likes INTEGER NOT NULL DEFAULT 0,
                        video_count INTEGER NOT NULL DEFAULT 0,
                        videos TEXT NOT NULL DEFAULT '[]'
                    );
                    CREATE INDEX IF NOT EXISTS idx_scan_sec_ts
                        ON scan_history(sec_uid, timestamp);

                    CREATE TABLE IF NOT EXISTS alerts (
                        id INTEGER PRIMARY KEY,
                        timestamp REAL NOT NULL,
                        datetime TEXT NOT NULL,
                        type TEXT NOT NULL,
                        sec_uid TEXT NOT NULL DEFAULT '',
                        nickname TEXT NOT NULL DEFAULT '',
                        message TEXT NOT NULL DEFAULT '',
                        detail TEXT NOT NULL DEFAULT '{}',
                        read INTEGER NOT NULL DEFAULT 0,
                        dedup_key TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_alerts_type_ts
                        ON alerts(type, timestamp);
                    """
                )
                _conn.commit()
                _migrate_from_json_locked()
    return _conn


def _migrate_from_json_locked():
    """将旧 JSON 数据导入 SQLite（幂等，仅执行一次；JSON 保留为备份）。"""
    if os.path.exists(MIGRATED_FLAG):
        return
    migrated = False
    # 扫描历史
    if os.path.exists(HISTORY_FILE):
        history = _load_json(HISTORY_FILE, [])
        if history:
            _conn.executemany(
                "INSERT OR IGNORE INTO scan_history"
                " (timestamp, datetime, sec_uid, nickname, total_likes, video_count, videos)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        h.get("timestamp", 0.0),
                        h.get("datetime", ""),
                        h.get("sec_uid", ""),
                        h.get("nickname", ""),
                        int(h.get("total_likes", 0) or 0),
                        int(h.get("video_count", 0) or 0),
                        json.dumps(h.get("videos", []), ensure_ascii=False),
                    )
                    for h in history
                ],
            )
            migrated = True
    # 告警
    if os.path.exists(ALERTS_FILE):
        alerts = _load_json(ALERTS_FILE, [])
        if alerts:
            _conn.executemany(
                "INSERT OR IGNORE INTO alerts"
                " (id, timestamp, datetime, type, sec_uid, nickname, message, detail, read, dedup_key)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        a.get("id", int(a.get("timestamp", time.time()) * 1000)),
                        a.get("timestamp", 0.0),
                        a.get("datetime", ""),
                        a.get("type", a.get("alert_type", "")),
                        a.get("sec_uid", ""),
                        a.get("nickname", ""),
                        a.get("message", ""),
                        json.dumps(a.get("detail", {}), ensure_ascii=False),
                        1 if a.get("read") else 0,
                        a.get("dedup_key"),
                    )
                    for a in alerts
                ],
            )
            migrated = True
    _conn.commit()
    # 写入迁移标记（即使没有旧数据也标记，避免重复检查）
    try:
        with open(MIGRATED_FLAG, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
    except IOError:
        pass
    if migrated:
        print(f"[存储] 已从旧 JSON 迁移数据到 SQLite（{DB_FILE}），旧 JSON 保留为备份")


def _row_to_scan(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["videos"] = json.loads(d.get("videos") or "[]")
    return d


def _row_to_alert(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["detail"] = json.loads(d.get("detail") or "{}")
    d["read"] = bool(d.get("read"))
    return d


def _all_history_asc() -> list:
    """按时间正序读取全部扫描历史（最多 HISTORY_KEEP 条）。"""
    with _conn_lock:
        cur = _get_conn().execute(
            "SELECT * FROM scan_history ORDER BY timestamp ASC, id ASC"
        )
        return [_row_to_scan(r) for r in cur.fetchall()]


# ==================== 扫描历史 ====================

def add_scan_record(sec_uid: str, nickname: str, videos: list, total_likes: int):
    """记录一次扫描结果"""
    record = {
        "timestamp": time.time(),
        "datetime": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sec_uid": sec_uid,
        "nickname": nickname,
        "total_likes": total_likes,
        "video_count": len(videos),
        "videos": [
            {
                "aweme_id": v["aweme_id"],
                "title": v.get("title", "")[:50],
                "digg_count": v.get("digg_count", 0),
                "type": v.get("type", "video"),
                "cover_url": v.get("cover_url", ""),
            }
            for v in videos
        ],
    }
    with _conn_lock:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO scan_history"
            " (timestamp, datetime, sec_uid, nickname, total_likes, video_count, videos)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record["timestamp"],
                record["datetime"],
                record["sec_uid"],
                record["nickname"],
                record["total_likes"],
                record["video_count"],
                json.dumps(record["videos"], ensure_ascii=False),
            ),
        )
        # 只保留最近 HISTORY_KEEP 条
        conn.execute(
            "DELETE FROM scan_history WHERE id NOT IN ("
            " SELECT id FROM scan_history ORDER BY timestamp DESC, id DESC LIMIT ?)",
            (HISTORY_KEEP,),
        )
        conn.commit()
    return record


def get_history(sec_uid: Optional[str] = None, limit: int = 50) -> list:
    """获取扫描历史，可按 sec_uid 过滤（最新在前）"""
    with _conn_lock:
        conn = _get_conn()
        if sec_uid:
            cur = conn.execute(
                "SELECT * FROM scan_history WHERE sec_uid = ?"
                " ORDER BY timestamp DESC, id DESC LIMIT ?",
                (sec_uid, limit),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM scan_history ORDER BY timestamp DESC, id DESC LIMIT ?",
                (limit,),
            )
        return [_row_to_scan(r) for r in cur.fetchall()]


def get_first_scan(sec_uid: str) -> Optional[dict]:
    """获取某个账号的第一次扫描记录（用于计算累计增量）"""
    with _conn_lock:
        cur = _get_conn().execute(
            "SELECT * FROM scan_history WHERE sec_uid = ?"
            " ORDER BY timestamp ASC, id ASC LIMIT 1",
            (sec_uid,),
        )
        row = cur.fetchone()
        return _row_to_scan(row) if row else None


def get_last_scan(sec_uid: str) -> Optional[dict]:
    """获取某个账号的最近一次扫描记录（用于计算相邻增量）"""
    with _conn_lock:
        cur = _get_conn().execute(
            "SELECT * FROM scan_history WHERE sec_uid = ?"
            " ORDER BY timestamp DESC, id DESC LIMIT 1",
            (sec_uid,),
        )
        row = cur.fetchone()
        return _row_to_scan(row) if row else None


def get_video_last_likes(sec_uid: str, aweme_id: str) -> Optional[dict]:
    """
    获取某个视频上一次扫描时的点赞数据（用于计算单个视频的相邻增量）。
    返回: {"digg_count": 123, "datetime": "2026-09-10 12:00:00"} 或 None
    """
    last = get_last_scan(sec_uid)
    if not last:
        return None
    for v in last.get("videos", []):
        if v.get("aweme_id") == aweme_id:
            return {
                "digg_count": v.get("digg_count", 0),
                "datetime": last.get("datetime", ""),
                "timestamp": last.get("timestamp", 0),
            }
    return None


def get_video_first_likes(sec_uid: str, aweme_id: str) -> Optional[dict]:
    """
    获取某个视频第一次被扫描时的点赞数据（用于计算累计增量）。
    返回: {"digg_count": 123, "datetime": "2026-09-10 12:00:00"} 或 None
    """
    first = get_first_scan(sec_uid)
    if not first:
        return None
    for v in first.get("videos", []):
        if v.get("aweme_id") == aweme_id:
            return {
                "digg_count": v.get("digg_count", 0),
                "datetime": first.get("datetime", ""),
                "timestamp": first.get("timestamp", 0),
            }
    return None


def get_all_video_last_likes(sec_uid: str) -> dict:
    """
    获取某个账号所有视频上一次扫描的点赞数，批量查询用。
    返回: {aweme_id: digg_count}
    """
    last = get_last_scan(sec_uid)
    if not last:
        return {}
    return {v["aweme_id"]: v.get("digg_count", 0) for v in last.get("videos", [])}


def get_video_history(limit: int = 200, sec_uid: str = None) -> list:
    """
    获取视频维度的扫描历史（扁平化，每个视频一行）。
    返回按时间倒序的列表，每条包含：视频信息、账号、点赞、增量、扫描时间。
    """
    history = _all_history_asc()
    # 先按时间正序遍历，记录每个视频上一次的点赞数
    video_last_likes = {}  # aweme_id -> 上一次的点赞数
    video_records = []

    for h in history:
        if sec_uid and h.get("sec_uid") != sec_uid:
            continue
        for v in h.get("videos", []):
            aweme_id = v["aweme_id"]
            current_likes = v.get("digg_count", 0)
            last_likes = video_last_likes.get(aweme_id)
            delta = current_likes - last_likes if last_likes is not None else None
            is_new = last_likes is None

            video_records.append({
                "aweme_id": aweme_id,
                "title": v.get("title", ""),
                "cover_url": v.get("cover_url", ""),
                "digg_count": current_likes,
                "last_likes": last_likes,
                "delta_from_last": delta,
                "is_new": is_new,
                "type": v.get("type", "video"),
                "sec_uid": h.get("sec_uid", ""),
                "nickname": h.get("nickname", ""),
                "datetime": h.get("datetime", ""),
                "timestamp": h.get("timestamp", 0),
            })
            video_last_likes[aweme_id] = current_likes

    video_records.reverse()
    return video_records[:limit]


def get_video_trend(aweme_id: str, limit: int = 50) -> dict:
    """
    获取单个视频的点赞增长历史（按时间正序）。
    返回: {"aweme_id": ..., "trend": [{time, digg_count, comment_count, share_count}], "title": ...}
    """
    history = _all_history_asc()
    trend = []
    title = ""
    for scan in history:
        for v in scan.get("videos", []):
            if v.get("aweme_id") == aweme_id:
                prev = trend[-1]["digg_count"] if trend else None
                delta = v.get("digg_count", 0) - prev if prev is not None else 0
                title = v.get("title", "")
                trend.append({
                    "time": scan.get("datetime", ""),
                    "timestamp": scan.get("timestamp", 0),
                    "digg_count": v.get("digg_count", 0),
                    "comment_count": v.get("comment_count", 0),
                    "share_count": v.get("share_count", 0),
                    "delta": delta,
                })
                break
    return {
        "aweme_id": aweme_id,
        "trend": trend[-limit:],
        "title": title,
    }


def get_hot_videos(limit: int = 10) -> list:
    """
    获取热门视频排行榜（基于连续增长趋势，不是单次增量）。
    规则：
    - 至少连续2次扫描增量>0，才认为有增长趋势，进热门榜
    - 按最近3次平均增量排序，不是单次数据
    - 新视频（第一次出现）单独放，不参与热门排名
    """
    history = _all_history_asc()
    if len(history) < 2:
        return []

    account_history = {}
    for h in history:
        uid = h.get("sec_uid", "")
        if not uid:
            continue
        account_history.setdefault(uid, []).append(h)

    hot = []
    new_videos = []
    for uid, scans in account_history.items():
        if not scans:
            continue
        latest = scans[-1]

        recent_scans = scans[-3:]
        video_likes_history = {}
        for scan in recent_scans:
            for v in scan.get("videos", []):
                aid = v["aweme_id"]
                video_likes_history.setdefault(aid, []).append(v.get("digg_count", 0))

        for v in latest.get("videos", []):
            aid = v["aweme_id"]
            current = v.get("digg_count", 0)
            likes_series = video_likes_history.get(aid, [])

            if len(likes_series) < 2:
                new_videos.append({
                    "aweme_id": aid,
                    "title": v.get("title", ""),
                    "cover_url": v.get("cover_url", ""),
                    "nickname": latest.get("nickname", uid[:15]),
                    "current_likes": current,
                    "delta": 0,
                    "is_new": True,
                    "growth_days": len(likes_series) - 1,
                })
                continue

            deltas = [likes_series[i] - likes_series[i - 1] for i in range(1, len(likes_series))]
            positive_deltas = sum(1 for d in deltas if d > 0)
            if positive_deltas < 2:
                continue

            avg_delta = sum(deltas) / len(deltas)
            latest_delta = deltas[-1]

            hot.append({
                "aweme_id": aid,
                "title": v.get("title", ""),
                "cover_url": v.get("cover_url", ""),
                "nickname": latest.get("nickname", uid[:15]),
                "current_likes": current,
                "delta": latest_delta,
                "avg_delta": round(avg_delta, 1),
                "growth_days": len(likes_series) - 1,
                "is_new": False,
            })

    hot.sort(key=lambda x: (x["avg_delta"], x["current_likes"]), reverse=True)
    new_videos.sort(key=lambda x: x["current_likes"], reverse=True)
    result = new_videos + hot
    return result[:limit]


# ==================== 告警记录 ====================

def add_alert(alert_type: str, sec_uid: str, nickname: str, message: str, detail: dict = None,
              dedup_key: str = None, dedup_window: int = 3600):
    """
    添加一条告警。

    :param dedup_key: 去重键。非空时，窗口内（默认 1 小时）出现相同去重键的告警会被跳过，
                      避免同一视频/评论在连续扫描中被反复告警（告警降噪）。
    :param dedup_window: 去重窗口（秒），默认 3600。
    """
    now = time.time()
    with _conn_lock:
        conn = _get_conn()
        if dedup_key:
            cur = conn.execute(
                "SELECT * FROM alerts WHERE dedup_key = ? AND timestamp > ? LIMIT 1",
                (dedup_key, now - dedup_window),
            )
            existing = cur.fetchone()
            if existing:
                print(f"[告警] [{alert_type}] 窗口内重复告警已跳过: {message}")
                return _row_to_alert(existing)
        # 主键 = 毫秒时间戳，但同一毫秒可能写入多条 → 保证单调递增唯一
        row = conn.execute("SELECT MAX(id) FROM alerts").fetchone()
        alert_id = max(int(now * 1000), (row[0] or 0) + 1)
        alert = {
            "id": alert_id,
            "timestamp": now,
            "datetime": time.strftime("%Y-%m-%d %H:%M:%S"),
            "type": alert_type,
            "sec_uid": sec_uid,
            "nickname": nickname,
            "message": message,
            "detail": detail or {},
            "read": False,
            "dedup_key": dedup_key,
        }
        conn.execute(
            "INSERT INTO alerts"
            " (id, timestamp, datetime, type, sec_uid, nickname, message, detail, read, dedup_key)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                alert["id"], alert["timestamp"], alert["datetime"], alert["type"],
                alert["sec_uid"], alert["nickname"], alert["message"],
                json.dumps(alert["detail"], ensure_ascii=False),
                0, alert["dedup_key"],
            ),
        )
        # 只保留最近 ALERTS_KEEP 条
        conn.execute(
            "DELETE FROM alerts WHERE id NOT IN ("
            " SELECT id FROM alerts ORDER BY timestamp DESC, id DESC LIMIT ?)",
            (ALERTS_KEEP,),
        )
        conn.commit()
    print(f"[告警] [{alert_type}] {nickname}: {message}")
    return alert


def get_alerts(unread_only: bool = False, limit: int = 100) -> list:
    """获取告警列表（最新在前）"""
    with _conn_lock:
        conn = _get_conn()
        if unread_only:
            cur = conn.execute(
                "SELECT * FROM alerts WHERE read = 0"
                " ORDER BY timestamp DESC, id DESC LIMIT ?",
                (limit,),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM alerts ORDER BY timestamp DESC, id DESC LIMIT ?",
                (limit,),
            )
        return [_row_to_alert(r) for r in cur.fetchall()]


def mark_alert_read(alert_id: int):
    """标记告警已读"""
    with _conn_lock:
        _get_conn().execute("UPDATE alerts SET read = 1 WHERE id = ?", (alert_id,))
        _get_conn().commit()


def mark_all_alerts_read():
    """标记所有告警已读"""
    with _conn_lock:
        _get_conn().execute("UPDATE alerts SET read = 1")
        _get_conn().commit()


def clear_alerts():
    """清空告警"""
    with _conn_lock:
        _get_conn().execute("DELETE FROM alerts")
        _get_conn().commit()


# ==================== 扫描进度 ====================

def set_scan_progress(progress: dict):
    """保存当前扫描进度"""
    _save_json(PROGRESS_FILE, progress)


def get_scan_progress() -> dict:
    """获取当前扫描进度"""
    return _load_json(PROGRESS_FILE, None)


def clear_scan_progress():
    """清除扫描进度"""
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)


# ==================== 账号备注 ====================

def get_account_notes() -> dict:
    """获取所有账号备注 {sec_uid: note}"""
    return _load_json(ACCOUNT_NOTES_FILE, {})


def save_account_note(sec_uid: str, note: str):
    """保存账号备注"""
    notes = get_account_notes()
    notes[sec_uid] = note
    _save_json(ACCOUNT_NOTES_FILE, notes)


def delete_account_note(sec_uid: str):
    """删除账号备注"""
    notes = get_account_notes()
    if sec_uid in notes:
        del notes[sec_uid]
        _save_json(ACCOUNT_NOTES_FILE, notes)


# ==================== 评论管理 ====================

def _load_comment_replies() -> dict:
    return _load_json(COMMENT_REPLIES_FILE, {})


def _save_comment_replies(data: dict):
    _save_json(COMMENT_REPLIES_FILE, data)


def get_comments(status: str = "all") -> list:
    """
    获取评论关键词命中列表，合并回复状态。
    status: all=全部, pending=待回复, replied=已回复
    """
    with _conn_lock:
        cur = _get_conn().execute(
            "SELECT * FROM alerts WHERE type = 'comment_keyword'"
            " ORDER BY timestamp DESC, id DESC"
        )
        alerts = [_row_to_alert(r) for r in cur.fetchall()]
    replies = _load_comment_replies()
    comments = []
    for a in alerts:
        reply_info = replies.get(str(a.get("id")), {})
        d = a.get("detail", {})
        comments.append({
            "id": a.get("id"),
            "timestamp": a.get("timestamp"),
            "datetime": a.get("datetime"),
            "sec_uid": a.get("sec_uid"),
            "nickname": a.get("nickname"),
            "video_id": d.get("video_id", ""),
            "video_title": d.get("video_title", ""),
            "comment_author": d.get("comment_author", ""),
            "comment_text": d.get("comment_text", ""),
            "matched_keyword": d.get("matched_keyword", ""),
            "suggested_reply": reply_info.get("custom_reply") or d.get("suggested_reply", ""),
            "replied": reply_info.get("replied", False),
            "replied_at": reply_info.get("replied_at", ""),
        })
    if status == "pending":
        comments = [c for c in comments if not c["replied"]]
    elif status == "replied":
        comments = [c for c in comments if c["replied"]]
    return list(reversed(comments))


def mark_comment_replied(alert_id: int, custom_reply: str = None):
    """标记评论已回复，可同时保存自定义拟回复"""
    replies = _load_comment_replies()
    key = str(alert_id)
    if key not in replies:
        replies[key] = {}
    replies[key]["replied"] = True
    replies[key]["replied_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if custom_reply:
        replies[key]["custom_reply"] = custom_reply
    _save_comment_replies(replies)


def mark_comment_unreplied(alert_id: int):
    """标记评论未回复"""
    replies = _load_comment_replies()
    key = str(alert_id)
    if key in replies:
        replies[key]["replied"] = False
        replies[key]["replied_at"] = ""
    _save_comment_replies(replies)


def update_comment_reply(alert_id: int, custom_reply: str):
    """更新拟回复内容"""
    replies = _load_comment_replies()
    key = str(alert_id)
    if key not in replies:
        replies[key] = {"replied": False, "replied_at": ""}
    replies[key]["custom_reply"] = custom_reply
    _save_comment_replies(replies)


# ==================== 监控配置 ====================

DEFAULT_CONFIG = {
    "interval_minutes": 30,       # 扫描间隔（分钟）
    "schedule_mode": "interval",  # interval=按间隔, fixed=每天指定时间
    "schedule_times": ["09:00", "12:00", "18:00"],  # 指定时间点列表（HH:MM）
    "cron_expression": "",        # 兼容字段，由 schedule_times 自动生成
    "like_threshold": 300,        # 点赞增量告警阈值
    "sec_uids": [],               # 监控的账号列表
    "max_videos": 20,             # 每次采集最大视频数
    "min_days": 0,                # 只采集最近N天发布的视频，0表示不限制
    "scroll_times": 5,            # 滚动次数
    "get_comments": True,         # 是否采集评论
    "max_comments": 20,           # 评论上限
    "comment_check_count": 2,     # 检查评论的视频数量（前N条），减少可提升速度
    "comment_keywords": ["好听", "什么歌", "求歌名", "BGM", "bgm", "歌名", "什么歌曲", "这是什么歌"],  # 评论关键词
    "enabled": False,             # 监控是否启用
    # 飞书配置
    "lark_enabled": False,        # 是否启用飞书表格同步
    "lark_app_id": "",            # 飞书应用 app_id
    "lark_app_secret": "",        # 飞书应用 app_secret
    "lark_spreadsheet_token": "", # 飞书电子表格 token
    "lark_sheet_id": "0",         # 工作表 id
    "lark_webhook": "",           # 飞书机器人 webhook（用于推送告警）
}


def get_config() -> dict:
    config = _load_json(CONFIG_FILE, DEFAULT_CONFIG.copy())
    for k, v in DEFAULT_CONFIG.items():
        if k not in config:
            config[k] = v
    return config


def save_config(config: dict) -> dict:
    current = get_config()
    current.update(config)
    _save_json(CONFIG_FILE, current)
    return current


# ==================== 当前登录用户资料 ====================

def get_user_profile() -> dict:
    """获取当前登录用户的资料（昵称、头像）"""
    return _load_json(PROFILE_FILE, {"nickname": "", "avatar": "", "updated_at": None})


def save_user_profile(nickname: str, avatar: str):
    """保存当前登录用户的资料"""
    profile = {
        "nickname": nickname,
        "avatar": avatar,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_json(PROFILE_FILE, profile)
    return profile


def clear_user_profile():
    """清除用户资料（退出登录时）"""
    if os.path.exists(PROFILE_FILE):
        os.remove(PROFILE_FILE)
