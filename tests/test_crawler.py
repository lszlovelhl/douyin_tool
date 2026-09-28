"""
爬虫工具函数单元测试
====================
覆盖：sec_uid 提取（各类抖音链接格式）。
运行：cd douyin_tool && python3 -m unittest tests.test_crawler -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler import DouyinCrawler


class TestExtractSecUid(unittest.TestCase):
    def test_raw_sec_uid(self):
        uid = "MS4wLjABAAAAabcdef123456"
        assert DouyinCrawler.extract_sec_uid(uid) == uid

    def test_user_url(self):
        uid = "MS4wLjABAAAAabcdef123456"
        assert DouyinCrawler.extract_sec_uid(
            f"https://www.douyin.com/user/{uid}"
        ) == uid

    def test_user_url_with_params(self):
        uid = "MS4wLjABAAAAabcdef123456"
        assert DouyinCrawler.extract_sec_uid(
            f"https://www.douyin.com/user/{uid}?previous_page=app_code_link"
        ) == uid

    def test_share_url(self):
        uid = "MS4wLjABAAAAabcdef123456"
        assert DouyinCrawler.extract_sec_uid(
            f"https://www.iesdouyin.com/share/user/{uid}"
        ) == uid

    def test_query_param(self):
        uid = "MS4wLjABAAAAabcdef123456"
        assert DouyinCrawler.extract_sec_uid(
            f"https://www.douyin.com/?sec_uid={uid}&foo=bar"
        ) == uid

    def test_empty_input(self):
        assert DouyinCrawler.extract_sec_uid("") is None
        assert DouyinCrawler.extract_sec_uid(None) is None

    def test_garbage(self):
        assert DouyinCrawler.extract_sec_uid("这不是链接") is None


if __name__ == "__main__":
    unittest.main(verbosity=2)
