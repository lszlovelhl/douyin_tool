"""
监控核心逻辑单元测试
====================
覆盖：歌名提取、评论关键词识别、点赞增量计算。
运行：cd douyin_tool && python3 -m unittest tests.test_monitor -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import monitor


# ==================== 歌名提取 ====================

class TestExtractSongName(unittest.TestCase):
    def test_book_title(self):
        assert monitor.extract_song_name("这首歌是《你是我的仰望》") == "你是我的仰望"

    def test_quoted(self):
        assert monitor.extract_song_name("求歌名，是“你是我的仰望”这首歌") == "你是我的仰望"

    def test_jiao(self):
        assert monitor.extract_song_name("这首歌叫什么名字") == "什么名字"

    def test_shi(self):
        assert monitor.extract_song_name("这首歌是海阔天空") == "海阔天空"

    def test_no_song(self):
        assert monitor.extract_song_name("好好听啊") == "未知"

    def test_overlong_rejected(self):
        # 超过 30 字的候选被拒绝，返回未知
        assert monitor.extract_song_name("是" + "长" * 40) == "未知"


# ==================== 评论关键词识别 ====================

class TestAnalyzeComments(unittest.TestCase):
    def test_match_keyword(self):
        comments = [{"text": "这首歌好好听", "author": "小明"}]
        results = monitor.analyze_comments(comments, ["好听"])
        assert len(results) == 1
        assert results[0]["matched_keyword"] == "好听"
        assert results[0]["author"] == "小明"
        assert "这首歌是" in results[0]["suggested_reply"]

    def test_no_match(self):
        comments = [{"text": "普通评论", "author": "小明"}]
        assert monitor.analyze_comments(comments, ["好听"]) == []

    def test_song_extracted_into_reply(self):
        comments = [{"text": "BGM是什么，是《海阔天空》吗", "author": "小红"}]
        results = monitor.analyze_comments(comments, ["BGM", "bgm"])
        assert len(results) == 1
        assert results[0]["song_name"] == "海阔天空"
        assert "海阔天空" in results[0]["suggested_reply"]

    def test_default_reply(self):
        comments = [{"text": "什么歌这么好听", "author": "小刚"}]
        results = monitor.analyze_comments(comments, ["什么歌"])
        assert len(results) == 1


# ==================== 点赞增量计算 ====================

class TestComputeDeltas(unittest.TestCase):
    def test_normal_increment(self):
        videos = [
            {"aweme_id": "a1", "digg_count": 1500, "title": "v1"},
            {"aweme_id": "a2", "digg_count": 800, "title": "v2"},
        ]
        last = {"a1": 1200, "a2": 800}
        result = monitor.compute_deltas(videos, last, threshold=300)
        assert result[0]["delta_from_last"] == 300
        assert result[0]["is_new"] is False
        assert result[1]["delta_from_last"] == 0

    def test_new_video(self):
        videos = [{"aweme_id": "a3", "digg_count": 500, "title": "new"}]
        result = monitor.compute_deltas(videos, {}, threshold=300)
        assert result[0]["is_new"] is True
        assert result[0]["delta_from_last"] == 500  # 新视频增量=当前点赞数
        assert result[0]["last_likes"] == 0

    def test_like_drop(self):
        videos = [{"aweme_id": "a1", "digg_count": 900, "title": "v1"}]
        last = {"a1": 1000}
        result = monitor.compute_deltas(videos, last, threshold=300)
        assert result[0]["delta_from_last"] == -100

    def test_threshold_not_involved_in_compute(self):
        # compute_deltas 只负责计算，阈值判断在调用方（返回全量带 delta 列表）
        videos = [{"aweme_id": "a1", "digg_count": 100, "title": "v1"}]
        last = {"a1": 0}
        result = monitor.compute_deltas(videos, last, threshold=300)
        assert result[0]["delta_from_last"] == 100


if __name__ == "__main__":
    unittest.main(verbosity=2)
