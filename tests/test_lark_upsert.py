"""
lark_client upsert 逻辑单元测试
================================
mock 飞书 API，验证：
- 新视频追加新行并更新索引
- 已存在视频更新原行（不重复追加）
- 非纯数字视频ID被跳过（防脏数据）
- 索引加载只收录合法视频ID
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lark_client import LarkClient, VIDEO_ID_RE


def _ok_response(json_body):
    resp = mock.Mock()
    resp.json.return_value = json_body
    return resp


def _values_response(rows):
    return _ok_response({
        "code": 0,
        "data": {"valueRange": {"values": rows}},
    })


class LarkUpsertTestCase(unittest.TestCase):
    def setUp(self):
        self.lc = LarkClient("app_id", "app_secret", "sptoken", "sheet99")
        self.lc._access_token = "fake_token"  # 跳过真实鉴权
        self.lc._token_expire_time = 1e18

    def test_video_id_regex(self):
        self.assertTrue(VIDEO_ID_RE.match("2868652201000000001"))
        self.assertFalse(VIDEO_ID_RE.match("3.7万"))
        self.assertFalse(VIDEO_ID_RE.match(""))
        self.assertFalse(VIDEO_ID_RE.match("abc123"))

    def test_load_index_only_numeric_ids(self):
        rows = [
            ["t", "账号A", "标题1", "2868652201000000001", "100", "90", "+10", "否", "正常"],
            ["t", "账号B", "标题2", "3.7万", "200", "190", "+10", "否", "正常"],   # 脏数据，忽略
            ["t", "账号C", "标题3", "6639870301200000002", "300", "290", "+10", "是", "正常"],
        ]
        with mock.patch.object(self.lc, "_get_access_token", return_value="t"):
            with mock.patch("requests.get", return_value=_values_response(rows)) as m:
                self.lc._load_video_rows()
        self.assertEqual(self.lc._video_row_cache, {
            "2868652201000000001": 2,
            "6639870301200000002": 4,
        })
        m.assert_called_once()

    def test_append_new_video(self):
        self.lc._video_row_cache = {"2868652201000000001": 2}
        with mock.patch.object(self.lc, "_get_access_token", return_value="t"):
            with mock.patch.object(self.lc, "ensure_header"), \
                 mock.patch.object(self.lc, "_write_row", return_value=True) as wr, \
                 mock.patch.object(self.lc, "_get_next_empty_row", return_value=3):
                ok = self.lc.append_video_record(
                    nickname="账号X", video_title="新视频", video_id="6639870301200000002",
                    current_likes=50, last_likes=0, delta=50, is_new=True)
        self.assertTrue(ok)
        wr.assert_called_once()
        # 写入第 3 行（表尾）
        self.assertEqual(wr.call_args.kwargs["row"], 3)
        # 索引已更新
        self.assertEqual(self.lc._video_row_cache["6639870301200000002"], 3)

    def test_update_existing_video(self):
        self.lc._video_row_cache = {"2868652201000000001": 5}
        with mock.patch.object(self.lc, "_get_access_token", return_value="t"):
            with mock.patch.object(self.lc, "ensure_header"), \
                 mock.patch.object(self.lc, "_write_row", return_value=True) as wr:
                ok = self.lc.append_video_record(
                    nickname="账号X", video_title="老视频", video_id="2868652201000000001",
                    current_likes=120, last_likes=100, delta=20, is_new=False)
        self.assertTrue(ok)
        # 更新原行 5，而不是追加
        self.assertEqual(wr.call_args.kwargs["row"], 5)
        # 索引行号不变
        self.assertEqual(self.lc._video_row_cache["2868652201000000001"], 5)

    def test_skip_invalid_video_id(self):
        with mock.patch.object(self.lc, "_get_access_token", return_value="t"):
            with mock.patch.object(self.lc, "ensure_header") as eh, \
                 mock.patch.object(self.lc, "_write_row") as wr:
                ok = self.lc.append_video_record(
                    nickname="账号X", video_title="脏数据", video_id="3.7万",
                    current_likes=1, last_likes=0, delta=1, is_new=False)
        self.assertFalse(ok)
        wr.assert_not_called()

    def test_row_data_layout(self):
        """写入行的 9 列与表头定义一致（视频ID在第4列）"""
        self.lc._video_row_cache = {}
        captured = {}

        def fake_write(row_data, row):
            captured["data"] = row_data
            captured["row"] = row
            return True

        with mock.patch.object(self.lc, "_get_access_token", return_value="t"):
            with mock.patch.object(self.lc, "ensure_header"), \
                 mock.patch.object(self.lc, "_write_row", side_effect=fake_write), \
                 mock.patch.object(self.lc, "_get_next_empty_row", return_value=2):
                self.lc.append_video_record(
                    nickname="账号Y", video_title="列校验", video_id="9999999999999999999",
                    current_likes=66, last_likes=55, delta=11, is_new=False)
        data = captured["data"]
        self.assertEqual(data[3], "9999999999999999999")  # 视频ID 在第 4 列


if __name__ == "__main__":
    unittest.main()
