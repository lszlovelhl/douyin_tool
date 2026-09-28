"""
storage 模块单元测试（SQLite 版）
================================
- 用临时目录隔离，不污染真实 data/
- 覆盖：扫描历史增删查、告警去重、上限裁剪、迁移幂等、并发写
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

# 让被测模块可导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import storage


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        # 隔离环境：临时 DATA_DIR，并重置全局连接
        self._tmp = tempfile.mkdtemp(prefix="douyin_storage_test_")
        self._old_data_dir = storage.DATA_DIR
        for attr in ("DATA_DIR", "HISTORY_FILE", "ALERTS_FILE", "CONFIG_FILE",
                     "PROFILE_FILE", "PROGRESS_FILE", "ACCOUNT_NOTES_FILE",
                     "COMMENT_REPLIES_FILE", "DB_FILE", "MIGRATED_FLAG"):
            setattr(storage, attr, os.path.join(self._tmp, attr.split("_")[0]))
        storage.DB_FILE = os.path.join(self._tmp, "monitor.db")
        storage.MIGRATED_FLAG = os.path.join(self._tmp, ".sqlite_migrated")
        storage._conn = None

    def tearDown(self):
        storage._conn = None
        try:
            storage._get_conn().close()
        except Exception:
            pass
        storage._conn = None
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _seed_json(self):
        """写入旧版 JSON 数据，模拟升级前状态"""
        os.makedirs(storage.DATA_DIR, exist_ok=True)
        hist = []
        for i in range(3):
            hist.append({
                "timestamp": 1000 + i,
                "datetime": f"2026-09-0{i+1} 10:00:00",
                "sec_uid": "sec_a",
                "nickname": "账号A",
                "total_likes": 100 + i,
                "video_count": 1,
                "videos": [{"aweme_id": f"vid{i}", "title": f"视频{i}",
                            "digg_count": 10 + i, "type": "video", "cover_url": ""}],
            })
        with open(storage.HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False)
        alerts = [{
            "id": 1, "timestamp": 2000.0, "datetime": "2026-09-02 11:00:00",
            "type": "like_threshold", "sec_uid": "sec_a", "nickname": "账号A",
            "message": "涨粉告警", "detail": {"v": 1}, "read": False, "dedup_key": "k1",
        }]
        with open(storage.ALERTS_FILE, "w", encoding="utf-8") as f:
            json.dump(alerts, f, ensure_ascii=False)

    # ---------- 迁移 ----------
    def test_migrate_from_json(self):
        self._seed_json()
        storage._get_conn()  # 触发迁移
        self.assertEqual(len(storage.get_history(limit=10)), 3)
        self.assertEqual(storage.get_history(limit=10)[0]["nickname"], "账号A")
        self.assertEqual(len(storage.get_alerts()), 1)
        self.assertTrue(os.path.exists(storage.MIGRATED_FLAG))
        # 旧 JSON 保留为备份
        self.assertTrue(os.path.exists(storage.HISTORY_FILE))

    def test_migrate_idempotent(self):
        self._seed_json()
        storage._get_conn()
        # 手动再跑一次迁移（模拟重复触发）
        storage._migrate_from_json_locked()
        self.assertEqual(len(storage.get_history(limit=10)), 3)
        self.assertEqual(len(storage.get_alerts()), 1)

    # ---------- 扫描历史 ----------
    def test_add_and_query_scan(self):
        rec = storage.add_scan_record("sec_b", "账号B",
                                      [{"aweme_id": "v9", "title": "新视频", "digg_count": 5}], 5)
        self.assertEqual(rec["nickname"], "账号B")
        hist = storage.get_history()
        self.assertEqual(hist[0]["sec_uid"], "sec_b")
        self.assertEqual(storage.get_last_scan("sec_b")["total_likes"], 5)
        self.assertEqual(storage.get_first_scan("sec_b")["datetime"], rec["datetime"])
        self.assertIsNone(storage.get_first_scan("sec_none"))

    def test_history_cap_500(self):
        for i in range(505):
            storage.add_scan_record("sec_c", f"账号{i}",
                                    [{"aweme_id": f"v{i}", "digg_count": i}], i)
        self.assertEqual(len(storage.get_history(limit=1000)), 500)
        # 最新在最前
        self.assertEqual(storage.get_history(limit=1)[0]["nickname"], "账号504")

    def test_video_likes_lookup(self):
        storage.add_scan_record("sec_d", "账号D",
                                [{"aweme_id": "v1", "digg_count": 10}], 10)
        storage.add_scan_record("sec_d", "账号D",
                                [{"aweme_id": "v1", "digg_count": 30},
                                 {"aweme_id": "v2", "digg_count": 7}], 37)
        self.assertEqual(storage.get_video_last_likes("sec_d", "v1")["digg_count"], 30)
        self.assertEqual(storage.get_video_first_likes("sec_d", "v1")["digg_count"], 10)
        self.assertEqual(storage.get_all_video_last_likes("sec_d"), {"v1": 30, "v2": 7})

    def test_video_history_flat(self):
        storage.add_scan_record("sec_e", "账号E",
                                [{"aweme_id": "v1", "digg_count": 10}], 10)
        storage.add_scan_record("sec_e", "账号E",
                                [{"aweme_id": "v1", "digg_count": 25}], 25)
        rows = storage.get_video_history()
        # 最新在前：第二次扫描 25
        self.assertEqual(rows[0]["digg_count"], 25)
        self.assertEqual(rows[0]["delta_from_last"], 15)
        self.assertFalse(rows[0]["is_new"])
        self.assertTrue(rows[1]["is_new"])

    def test_video_trend(self):
        storage.add_scan_record("sec_f", "账号F",
                                [{"aweme_id": "v9", "title": "趋势片", "digg_count": 10}], 10)
        storage.add_scan_record("sec_f", "账号F",
                                [{"aweme_id": "v9", "title": "趋势片", "digg_count": 18}], 18)
        trend = storage.get_video_trend("v9")
        self.assertEqual(trend["title"], "趋势片")
        self.assertEqual(len(trend["trend"]), 2)
        self.assertEqual(trend["trend"][-1]["digg_count"], 18)
        self.assertTrue(trend["trend"][-1]["time"])  # time 不再为空

    # ---------- 告警 ----------
    def test_add_alert_and_dedup(self):
        a1 = storage.add_alert("like_threshold", "sec_g", "账号G", "涨粉了",
                               detail={"v": 1}, dedup_key="dup:1")
        a2 = storage.add_alert("like_threshold", "sec_g", "账号G", "涨粉了(重复)",
                               detail={"v": 1}, dedup_key="dup:1")
        self.assertEqual(a1["id"], a2["id"])  # 窗口内重复被跳过，返回原告警
        self.assertEqual(len(storage.get_alerts()), 1)
        # 不同 dedup_key 正常新增
        a3 = storage.add_alert("like_threshold", "sec_g", "账号G", "另一条",
                               dedup_key="dup:2")
        self.assertEqual(len(storage.get_alerts()), 2)

    def test_alert_read_flow(self):
        a = storage.add_alert("new_video", "sec_h", "账号H", "新视频")
        self.assertTrue(storage.get_alerts()[0]["read"] is False)
        storage.mark_alert_read(a["id"])
        self.assertEqual(len(storage.get_alerts(unread_only=True)), 0)
        storage.mark_all_alerts_read()
        self.assertEqual(len(storage.get_alerts(unread_only=True)), 0)
        storage.clear_alerts()
        self.assertEqual(len(storage.get_alerts()), 0)

    def test_alert_cap_200(self):
        for i in range(205):
            storage.add_alert("like_threshold", "sec_i", f"账号{i}", f"告警{i}")
        self.assertEqual(len(storage.get_alerts(limit=1000)), 200)

    # ---------- 并发 ----------
    def test_concurrent_writes(self):
        errors = []

        def worker(n):
            try:
                for i in range(20):
                    storage.add_scan_record(f"sec_{n}", f"账号{n}",
                                            [{"aweme_id": f"{n}_{i}", "digg_count": i}], i)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(storage.get_history(limit=1000)), 100)


if __name__ == "__main__":
    unittest.main()
