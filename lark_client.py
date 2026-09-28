"""
飞书表格客户端
==============
负责将抖音监控数据写入飞书电子表格。

功能：
- 获取 tenant_access_token（自动缓存，过期刷新）
- 写入扫描记录到飞书表格
- 自动创建表头（首次运行时）
- 支持增量数据写入
"""
import time
import re
import requests
from typing import Optional

# 抖音 aweme_id 为纯数字（通常 19 位），用于 upsert 时校验列值是否为合法视频 ID，
# 避免把历史/手工编辑产生的脏数据（如"3.7万"）当成 ID 误匹配。
VIDEO_ID_RE = re.compile(r'^\d{6,}$')


class LarkClient:
    """飞书电子表格客户端"""

    def __init__(self, app_id: str, app_secret: str, spreadsheet_token: str, sheet_id: str = "0"):
        self.app_id = app_id
        self.app_secret = app_secret
        self.spreadsheet_token = spreadsheet_token
        self.sheet_id = sheet_id
        self._access_token: Optional[str] = None
        self._token_expire_time: float = 0
        # upsert 缓存：{video_id: 行号}，首次写入时懒加载全表，之后增量维护
        self._video_row_cache: Optional[dict] = None

    # ==================== Token 管理 ====================

    def _get_access_token(self) -> str:
        """获取 tenant_access_token，自动缓存和刷新"""
        now = time.time()
        if self._access_token and now < self._token_expire_time - 60:
            return self._access_token

        resp = requests.post(
            'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
            json={'app_id': self.app_id, 'app_secret': self.app_secret},
            timeout=10
        )
        data = resp.json()
        if data.get('code') != 0:
            raise Exception(f"获取飞书token失败: {data.get('msg')}")

        self._access_token = data['tenant_access_token']
        self._token_expire_time = now + data.get('expire', 7200)
        return self._access_token

    def _headers(self) -> dict:
        return {'Authorization': f'Bearer {self._get_access_token()}'}

    # ==================== 表格操作 ====================

    def get_sheet_info(self) -> dict:
        """获取表格信息，验证权限"""
        resp = requests.get(
            f'https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/{self.spreadsheet_token}',
            headers=self._headers(), timeout=10
        )
        return resp.json()

    def _ensure_sheet_id(self):
        """自动获取实际的 sheet_id（默认第一个工作表）"""
        if self.sheet_id and self.sheet_id != "0":
            return  # 已经有有效的 sheet_id
        try:
            resp = requests.get(
                f'https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/{self.spreadsheet_token}/sheets/query',
                headers=self._headers(), timeout=10
            )
            data = resp.json()
            if data.get('code') == 0:
                sheets = data.get('data', {}).get('sheets', [])
                if sheets:
                    self.sheet_id = sheets[0].get('sheet_id', '0')
                    print(f"[飞书] 自动获取 sheet_id: {self.sheet_id}")
        except Exception as e:
            print(f"[飞书] 获取 sheet_id 失败: {e}")

    def ensure_header(self):
        """确保表头存在，不存在则写入"""
        self._ensure_sheet_id()
        header = ['扫描时间', '抖音昵称', '视频标题', '视频ID',
                  '当前点赞', '上次点赞', '相邻增量', '是否新视频', '状态']
        # 读取第一行，看是否已有表头
        resp = requests.get(
            f'https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{self.spreadsheet_token}/values/{self.sheet_id}!A1:I1',
            headers=self._headers(), timeout=10
        )
        data = resp.json()
        if data.get('code') != 0:
            # 读取失败，可能表格为空，直接写入表头
            self._write_row(header, row=1)
            print("[飞书] 已创建表头")
            return

        values = data.get('data', {}).get('valueRange', {}).get('values', [])
        if not values or not values[0] or values[0][0] != '扫描时间':
            self._write_row(header, row=1)
            print("[飞书] 已创建表头")

    def _write_row(self, row_data: list, row: int):
        """写入一行数据"""
        range_str = f'{self.sheet_id}!A{row}:I{row}'
        resp = requests.put(
            f'https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{self.spreadsheet_token}/values',
            headers=self._headers(),
            json={
                'valueRange': {
                    'range': range_str,
                    'values': [row_data]
                }
            },
            timeout=10
        )
        result = resp.json()
        if result.get('code') != 0:
            print(f"[飞书] 写入失败: {result.get('msg')}")
            return False
        return True

    def _load_video_rows(self):
        """
        读取全表（第 2 行起，最多 5000 行），建立 video_id → 行号 映射。
        用于 upsert：同一视频再次扫描时更新原行，而不是重复追加。
        """
        try:
            resp = requests.get(
                f'https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{self.spreadsheet_token}/values/{self.sheet_id}!A2:I5000',
                headers=self._headers(), timeout=15
            )
            data = resp.json()
            if data.get('code') == 0:
                rows = data.get('data', {}).get('valueRange', {}).get('values', [])
                mapping = {}
                for i, row in enumerate(rows):
                    if not row:
                        continue
                    # 视频ID 在第 4 列（表头定义），仅接受纯数字 ID，脏数据不参与匹配
                    vid = row[3] if len(row) > 3 else ""
                    if VIDEO_ID_RE.match(str(vid).strip()):
                        mapping[str(vid).strip()] = i + 2  # 数据从第 2 行开始
                self._video_row_cache = mapping
                print(f"[飞书] upsert 索引已加载：{len(mapping)} 个视频")
                return
        except Exception as e:
            print(f"[飞书] 加载视频索引失败: {e}")
        self._video_row_cache = {}

    def refresh_video_rows(self):
        """强制刷新 upsert 索引（表格被外部编辑后可调用）"""
        self._video_row_cache = None

    def append_video_record(self, nickname: str, video_title: str, video_id: str,
                            current_likes: int, last_likes: int, delta: int,
                            is_new: bool, status: str = "正常") -> bool:
        """
        按视频维度 upsert 点赞记录到飞书表格：
        - 同一视频已存在 → 更新原行（反映最新状态）
        - 新视频 → 追加新行
        返回是否成功。
        """
        try:
            self.ensure_header()

            # 空视频 ID 直接跳过，避免写入脏数据
            if not VIDEO_ID_RE.match(str(video_id)):
                print(f"[飞书] 跳过无效视频ID: {str(video_id)[:30]}")
                return False

            scan_time = time.strftime("%Y-%m-%d %H:%M:%S")
            row_data = [
                scan_time,
                nickname,
                video_title[:50],
                video_id,
                current_likes,
                last_likes if last_likes > 0 else "-",
                f"+{delta}" if delta > 0 else str(delta),
                "是" if is_new else "否",
                status,
            ]

            # 懒加载 upsert 索引
            if self._video_row_cache is None:
                self._load_video_rows()

            existing_row = self._video_row_cache.get(video_id)
            if existing_row is not None:
                # 已存在：更新原行（索引行号不变）
                success = self._write_row(row_data, row=existing_row)
                if success:
                    print(f"[飞书] 已更新视频记录: {nickname} - {video_title[:20]} 点赞={current_likes} 增量=+{delta} (行{existing_row})")
                return success

            # 新视频：追加到表尾（缓存行数 + 表头行 + 1）
            next_row = self._get_next_empty_row()
            success = self._write_row(row_data, row=next_row)
            if success:
                self._video_row_cache[video_id] = next_row
                print(f"[飞书] 已写入视频记录: {nickname} - {video_title[:20]} 点赞={current_likes} 增量=+{delta}")
            return success
        except Exception as e:
            print(f"[飞书] 写入异常: {e}")
            return False

    def _get_next_empty_row(self) -> int:
        """获取下一个空行号"""
        try:
            resp = requests.get(
                f'https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{self.spreadsheet_token}/values/{self.sheet_id}!A:A',
                headers=self._headers(), timeout=10
            )
            data = resp.json()
            if data.get('code') == 0:
                values = data.get('data', {}).get('valueRange', {}).get('values', [])
                return len(values) + 1
        except Exception:
            pass
        return 2  # 默认从第2行开始（第1行是表头）

    def test_connection(self) -> dict:
        """测试连接，返回详细信息"""
        try:
            info = self.get_sheet_info()
            if info.get('code') == 0:
                title = info['data']['spreadsheet'].get('title', '')
                return {"success": True, "message": f"连接成功，表格: {title}"}
            else:
                return {"success": False, "message": f"连接失败: {info.get('msg')}", "code": info.get('code')}
        except Exception as e:
            return {"success": False, "message": str(e)}
