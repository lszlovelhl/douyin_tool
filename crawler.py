"""
抖音数据采集爬虫 - Playwright 版本
====================================
通过 Playwright 渲染页面，携带登录态 cookies 访问，
自动处理 a_bogus 签名等风控机制，稳定采集用户信息、视频列表和评论。

使用方式：
    from crawler import DouyinCrawler
    c = DouyinCrawler(cookies_file='cookies/douyin_cookies.json')
    c.start()
    user = c.get_user_info(sec_uid)
    videos = c.get_user_videos(sec_uid, max_count=20)
    comments = c.get_video_comments(aweme_id, max_count=20)
    c.close()
"""
import json
import re
import time
import requests
from typing import Optional
from playwright.sync_api import sync_playwright, Page, BrowserContext


class DouyinCrawler:
    """抖音数据采集器"""

    def __init__(self, cookies_file: str = "cookies/douyin_cookies.json",
                 headless: bool = True, timeout: int = 30000):
        self.cookies_file = cookies_file
        self.headless = headless
        self.timeout = timeout
        self._playwright = None
        self._browser = None
        self._context: Optional[BrowserContext] = None

    # ==================== 生命周期 ====================

    def start(self):
        """启动浏览器并加载 cookies"""
        self._playwright = sync_playwright().start()
        # macOS 上 Playwright 自动管理 Chromium，无需指定 executable_path
        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            channel="chrome",  # 使用系统安装的 Google Chrome
            args=[
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
            ]
        )
        self._context = self._browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/120.0.0.0 Safari/537.36'
        )
        self._load_cookies()
        print("[爬虫] 浏览器已启动，cookies 已加载")

    def close(self):
        """关闭浏览器"""
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
        print("[爬虫] 浏览器已关闭")

    def _load_cookies(self):
        """从 JSON 文件加载 cookies 到浏览器上下文"""
        try:
            with open(self.cookies_file, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
            # 转换为 Playwright 格式
            pw_cookies = []
            for c in cookies:
                if not c.get('name'):
                    continue
                pw_cookies.append({
                    'name': c['name'],
                    'value': c['value'],
                    'domain': c.get('domain', '.douyin.com'),
                    'path': c.get('path', '/'),
                    'httpOnly': c.get('httpOnly', False),
                    'secure': c.get('secure', False),
                    'sameSite': 'Lax',
                })
            self._context.add_cookies(pw_cookies)
            print(f"[爬虫] 已加载 {len(pw_cookies)} 个 cookies")
        except FileNotFoundError:
            print(f"[爬虫] 警告: cookies 文件 {self.cookies_file} 不存在，将以未登录状态访问")

    def _new_page(self) -> Page:
        return self._context.new_page()

    # ==================== 工具方法 ====================

    @staticmethod
    def parse_count(text: str) -> int:
        """将 '1.2万'、'3456' 等格式转为整数"""
        if not text:
            return 0
        text = str(text).strip().replace(',', '')
        if '万' in text:
            try:
                return int(float(text.replace('万', '')) * 10000)
            except ValueError:
                return 0
        try:
            return int(text)
        except ValueError:
            return 0

    @staticmethod
    def extract_sec_uid(input_str: str) -> Optional[str]:
        """
        从用户输入中提取 sec_uid。
        支持：
          - 直接的 sec_uid: MS4wLjABAAAA...
          - 主页链接: https://www.douyin.com/user/MS4wLjABAAAA...
          - 带参数的链接: https://www.douyin.com/user/MS4wLjABAAAA...?previous_page=...
          - 分享页链接: https://www.iesdouyin.com/share/user/MS4wLjABAAAA...
          - 短链接: https://v.douyin.com/xxxxx/ （自动跳转解析）
        返回提取到的 sec_uid，无法识别返回 None。
        """
        if not input_str:
            return None
        s = input_str.strip()
        import re
        # 情况1: 完整 URL (douyin.com/user/ 或 iesdouyin.com/share/user/)
        m = re.search(r'(?:douyin\.com/user|iesdouyin\.com/share/user)/([A-Za-z0-9_\-]+)', s)
        if m:
            return m.group(1)
        # 情况2: URL 查询参数中的 sec_uid
        m = re.search(r'[?&]sec_uid=([A-Za-z0-9_\-]+)', s)
        if m:
            return m.group(1)
        # 情况3: 短链接 / 分享链接（v.douyin.com）- 需要跳转解析
        if 'v.douyin.com' in s:
            return DouyinCrawler._resolve_short_url(s)
        # 情况4: 直接是 sec_uid（以 MS4w 开头，长度足够）
        if re.match(r'^[A-Za-z0-9_\-]{20,}$', s):
            return s
        return None

    @staticmethod
    def _resolve_short_url(short_url: str) -> Optional[str]:
        """解析抖音短链接，跳转后提取 sec_uid"""
        import re
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9',
            }
            resp = requests.get(short_url, headers=headers, allow_redirects=True, timeout=10)
            final_url = resp.url
            # 从跳转后的 URL 提取 sec_uid
            m = re.search(r'(?:douyin\.com/user|iesdouyin\.com/share/user)/([A-Za-z0-9_\-]+)', final_url)
            if m:
                return m.group(1)
            m = re.search(r'[?&]sec_uid=([A-Za-z0-9_\-]+)', final_url)
            if m:
                return m.group(1)
            print(f"[短链接解析] 跳转后无法提取sec_uid: {final_url[:80]}")
            return None
        except Exception as e:
            print(f"[短链接解析] 失败: {short_url} -> {e}")
            return None

    @staticmethod
    def extract_my_sec_uid_from_cookies(cookies: list) -> Optional[str]:
        """从 cookies 中提取当前登录用户的 sec_uid（FOLLOW_LIVE_POINT_INFO 等字段中包含）"""
        import re
        for c in cookies:
            val = c.get('value', '')
            m = re.search(r'(MS4wLjABAAAA[A-Za-z0-9_\-]+)', val)
            if m:
                return m.group(1)
        return None

    def check_login_status(self) -> dict:
        """
        检测当前 cookies 是否有效（是否处于登录状态）。
        同时提取当前登录用户的头像和昵称。
        策略：从 cookies 中提取当前用户 sec_uid，访问自己的主页验证并提取资料。
        返回: {loggedIn: bool, nickname: str, avatar: str, message: str}
        """
        page = self._new_page()
        try:
            # 从 cookies 中提取当前用户的 sec_uid
            my_sec_uid = None
            try:
                with open(self.cookies_file, 'r', encoding='utf-8') as f:
                    cookies = json.load(f)
                my_sec_uid = self.extract_my_sec_uid_from_cookies(cookies)
            except Exception:
                pass

            if my_sec_uid:
                # 访问自己的主页，既能验证登录，又能提取昵称头像
                page.goto(f'https://www.douyin.com/user/{my_sec_uid}',
                          wait_until='domcontentloaded', timeout=20000)
                page.wait_for_timeout(4000)

                result = page.evaluate('''() => {
                    const userInfo = document.querySelector('[data-e2e="user-info"]');
                    if (!userInfo) return { loggedIn: false, nickname: '', avatar: '' };
                    const nameEl = userInfo.querySelector('h1');
                    const avatarEl = userInfo.querySelector('img');
                    const fansEl = document.querySelector('[data-e2e="user-info-fans"]');
                    return {
                        loggedIn: !!fansEl,
                        nickname: nameEl ? nameEl.textContent.trim() : '',
                        avatar: avatarEl ? avatarEl.src : '',
                    };
                }''')
                result['message'] = '登录状态有效' if result.get('loggedIn') else '未检测到登录态，可能 cookies 已过期'
            else:
                # 无法从 cookies 提取 sec_uid，用首页检测
                page.goto('https://www.douyin.com/', wait_until='domcontentloaded', timeout=20000)
                page.wait_for_timeout(3000)
                result = page.evaluate('''() => {
                    let loggedIn = false;
                    let nickname = '';
                    let avatar = '';
                    const imgs = document.querySelectorAll('img');
                    for (const img of imgs) {
                        if (img.src && img.src.includes('douyinpic') &&
                            (img.src.includes('avatar') || img.src.includes('aweme-avatar'))) {
                            if (!avatar) avatar = img.src;
                            if (img.alt && img.alt.includes('头像')) {
                                nickname = img.alt.replace('头像', '').trim();
                            }
                        }
                    }
                    const loginBtn = document.querySelector('[data-e2e="login-button"], button[data-e2e*="login"]');
                    loggedIn = !loginBtn && !!avatar;
                    return { loggedIn, nickname, avatar };
                }''')
                result['message'] = '登录状态有效' if result.get('loggedIn') else '未检测到登录态'

            print(f"[登录检测] {'已登录' if result['loggedIn'] else '未登录'}, 用户: {result.get('nickname', '')}")
            return result
        except Exception as e:
            print(f"[登录检测] 检测失败: {e}")
            return {"loggedIn": False, "nickname": "", "avatar": "", "message": f"检测失败: {e}"}
        finally:
            page.close()

    # ==================== 用户信息 ====================

    def get_user_info(self, sec_uid: str) -> dict:
        """
        获取用户信息。
        返回: {nickname, douyin_id, sec_uid, follower_count, following_count,
               total_favorited, aweme_count, signature, location, avatar}
        """
        print(f"[爬虫] 获取用户信息: {sec_uid[:30]}...")
        page = self._new_page()
        try:
            page.goto(f'https://www.douyin.com/user/{sec_uid}',
                      wait_until='domcontentloaded', timeout=self.timeout)
            page.wait_for_timeout(5000)

            info = page.evaluate('''() => {
                const result = {};
                const userInfo = document.querySelector('[data-e2e="user-info"]');
                const infoText = userInfo ? userInfo.textContent : '';

                // 昵称
                const nameEl = userInfo ? userInfo.querySelector('h1') : null;
                result.nickname = nameEl ? nameEl.textContent.trim() : '';

                // 粉丝/获赞
                const fansEl = document.querySelector('[data-e2e="user-info-fans"]');
                const likeEl = document.querySelector('[data-e2e="user-info-like"]');
                result.follower_count = fansEl ? fansEl.textContent.trim().replace(/^粉丝/, '') : '0';
                result.total_favorited = likeEl ? likeEl.textContent.trim().replace(/^获赞/, '') : '0';

                // 关注数
                const followMatch = infoText.match(/关注[\\s\\n]*(\\d+[.\\d]*万?)/);
                result.following_count = followMatch ? followMatch[1] : '0';

                // 抖音号
                const idMatch = infoText.match(/抖音号[：:]\\s*(\\d+)/);
                result.douyin_id = idMatch ? idMatch[1] : '';

                // 地区
                const locMatch = infoText.match(/([\\u4e00-\\u9fa5]+·[\\u4e00-\\u9fa5]+)/);
                result.location = locMatch ? locMatch[1] : '';

                // 简介
                if (idMatch && locMatch) {
                    const locIdx = infoText.indexOf(locMatch[1]);
                    result.signature = infoText.substring(locIdx + locMatch[1].length).trim().substring(0, 100);
                } else {
                    result.signature = '';
                }

                // 头像
                const avatarEl = userInfo ? userInfo.querySelector('img') : null;
                result.avatar = avatarEl ? avatarEl.src : '';

                // 作品数
                const tabCount = document.querySelector('[data-e2e="user-tab-count"]');
                result.aweme_count = tabCount ? tabCount.textContent.trim() : '0';

                return result;
            }''')

            info['sec_uid'] = sec_uid
            print(f"[爬虫] 用户: {info.get('nickname', '')}, 粉丝: {info.get('follower_count', '')}, 获赞: {info.get('total_favorited', '')}")
            return info
        finally:
            page.close()

    # ==================== 视频列表 ====================

    def get_user_videos(self, sec_uid: str, max_count: int = 30,
                        scroll_times: int = 5, min_days: int = 0) -> list[dict]:
        """
        获取用户视频/图文列表。
        Args:
            min_days: 只返回最近N天发布的视频，0表示不限制
        返回: [{aweme_id, title, digg_count, digg_count_text, type,
                cover_url, video_url, create_time_text, create_days_ago}]
        """
        print(f"[爬虫] 获取视频列表: {sec_uid[:30]}... (最多{max_count}条, 最近{min_days}天)")
        page = self._new_page()
        try:
            page.goto(f'https://www.douyin.com/user/{sec_uid}',
                      wait_until='domcontentloaded', timeout=self.timeout)
            page.wait_for_timeout(5000)

            # 滚动加载更多
            for i in range(scroll_times):
                page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                page.wait_for_timeout(1500)

            # 提取视频/图文数据（同时匹配 /video/ 和 /note/）
            videos = page.evaluate('''() => {
                const results = [];
                const seen = new Set();
                const list = document.querySelector('[data-e2e="user-post-list"]');
                if (!list) return results;

                const cards = list.querySelectorAll('li, div[class*="item"]');
                cards.forEach(card => {
                    const link = card.querySelector('a[href*="/video/"], a[href*="/note/"]');
                    if (!link) return;
                    const href = link.getAttribute('href') || '';
                    const match = href.match(/\\/(video|note)\\/(\\d+)/);
                    if (!match) return;
                    const awemeId = match[2];
                    if (seen.has(awemeId)) return;
                    seen.add(awemeId);

                    const type = match[1];
                    const cardText = card.textContent || '';

                    // 点赞数：卡片文本开头的数字
                    let diggText = '0';
                    const diggMatch = cardText.match(/^(\\d+[.\\d]*万?)/);
                    if (diggMatch) diggText = diggMatch[1];

                    // 标题：去掉开头的点赞数
                    let title = (link.getAttribute('title') || link.textContent || '').trim();
                    title = title.replace(/^\\d+[.\\d]*万?/, '').trim();

                    // 封面
                    const coverEl = card.querySelector('img');
                    const cover = coverEl ? coverEl.src : '';

                    // 发布时间：尝试从卡片中提取
                    let createTimeText = '';
                    // 匹配相对时间：X天前、X小时前、X分钟前、刚刚
                    const relativeMatch = cardText.match(/(\\d+天前|\\d+小时前|\\d+分钟前|刚刚|昨天|前天)/);
                    if (relativeMatch) createTimeText = relativeMatch[1];
                    // 匹配绝对时间：MM-DD 或 YYYY-MM-DD
                    if (!createTimeText) {
                        const dateMatch = cardText.match(/(\\d{4}-\\d{2}-\\d{2}|\\d{2}-\\d{2})/);
                        if (dateMatch) createTimeText = dateMatch[1];
                    }
                    // 从子元素中找时间类元素
                    if (!createTimeText) {
                        const timeEls = card.querySelectorAll('[class*="time"], [class*="date"], span[class*="time"]');
                        for (const el of timeEls) {
                            const t = (el.textContent || '').trim();
                            if (t && t.length < 20) { createTimeText = t; break; }
                        }
                    }

                    results.push({
                        aweme_id: awemeId,
                        title: title,
                        digg_count_text: diggText,
                        type: type,
                        cover_url: cover,
                        video_url: href.startsWith('http') ? href : 'https://www.douyin.com' + href,
                        create_time_text: createTimeText,
                    });
                });
                return results;
            }''')

            # 计算发布时间距今天数，并按天数筛选
            import datetime
            today = datetime.date.today()
            for v in videos:
                v['create_days_ago'] = self._parse_create_time(v.get('create_time_text', ''), today)

            if min_days > 0:
                videos = [v for v in videos if v['create_days_ago'] is not None and v['create_days_ago'] <= min_days]

            videos = videos[:max_count]

            # 转换点赞数为数字
            for v in videos:
                v['digg_count'] = self.parse_count(v['digg_count_text'])

            print(f"[爬虫] 视频列表获取完成，共 {len(videos)} 条")
            return videos
        finally:
            page.close()

    @staticmethod
    def _parse_create_time(text: str, today) -> Optional[int]:
        """将发布时间文本转为距今天数，无法解析返回 None"""
        if not text:
            return None
        import datetime
        try:
            if '刚刚' in text or '分钟前' in text:
                return 0
            if '小时前' in text:
                return 0
            if '昨天' in text:
                return 1
            if '前天' in text:
                return 2
            if '天前' in text:
                m = re.match(r'(\d+)天前', text)
                if m:
                    return int(m.group(1))
            # 绝对日期 YYYY-MM-DD
            m = re.match(r'(\d{4})-(\d{2})-(\d{2})', text)
            if m:
                d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                return (today - d).days
            # 绝对日期 MM-DD（默认今年）
            m = re.match(r'(\d{2})-(\d{2})', text)
            if m:
                d = datetime.date(today.year, int(m.group(1)), int(m.group(2)))
                if d > today:
                    d = d.replace(year=today.year - 1)
                return (today - d).days
        except Exception:
            pass
        return None

    # ==================== 评论采集 ====================

    def get_video_comments(self, aweme_id: str, max_count: int = 20,
                           video_type: str = "video") -> list[dict]:
        """
        获取视频/图文评论。
        返回: [{text, author, digg_count, create_time}]
        """
        print(f"[爬虫] 获取评论: {aweme_id} (最多{max_count}条)")
        page = self._new_page()
        try:
            url = f'https://www.douyin.com/{video_type}/{aweme_id}'
            page.goto(url, wait_until='domcontentloaded', timeout=self.timeout)
            page.wait_for_timeout(3000)

            # 关闭可能的弹窗
            for btn_text in ['取消', '知道了', '关闭', '暂不']:
                try:
                    btn = page.get_by_text(btn_text, exact=True).first
                    if btn and btn.is_visible():
                        btn.click(timeout=1000)
                        page.wait_for_timeout(500)
                except:
                    pass

            # 点击评论图标，展开评论列表（抖音网页版评论需要点击才会全屏展开）
            comment_clicked = False
            comment_icons = page.query_selector_all('[data-e2e*="comment"], [class*="comment-icon"], [class*="CommentIcon"]')
            for el in comment_icons:
                try:
                    if el.is_visible():
                        el.click()
                        comment_clicked = True
                        page.wait_for_timeout(3000)
                        break
                except:
                    pass

            if not comment_clicked:
                print("[爬虫] 未找到评论图标，尝试滚动加载")
                page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                page.wait_for_timeout(2000)

            # 滚动加载更多评论
            for i in range(3):
                page.mouse.wheel(0, 800)
                page.wait_for_timeout(1000)

            comments = page.evaluate('''(maxCount) => {
                const results = [];
                const seen = new Set();

                // 排除干扰关键词
                const excludeAuthors = ['全部评论', '3s后播放', '3s 后播放', '留下你的精彩评论',
                    '推荐视频', '展开', '收起', '回复', '分享', '举报'];

                // 抖音评论展开后，评论项包含头像、用户名、评论文本
                document.querySelectorAll('div').forEach(el => {
                    if (results.length >= maxCount) return;
                    const imgs = el.querySelectorAll('img');
                    const text = el.innerText ? el.innerText.trim() : '';
                    if (imgs.length < 1 || !text || !text.includes('\\n')) return;
                    if (text.length < 5 || text.length > 300) return;

                    const lines = text.split('\\n').map(l => l.trim()).filter(l => l);
                    if (lines.length < 2 || lines.length > 5) return;

                    const author = lines[0];
                    const commentText = lines[1];

                    // 排除干扰
                    if (author.length < 1 || author.length > 25) return;
                    if (commentText.length < 2 || commentText.length > 200) return;
                    if (/^\\d+$/.test(author)) return;  // 纯数字
                    if (/^\\d{2}:\\d{2}/.test(author)) return;  // 时间格式（推荐视频）
                    if (/周前|天前|小时前|分钟前|秒前/.test(author)) return;
                    let isExclude = false;
                    for (const kw of excludeAuthors) {
                        if (author.includes(kw) || commentText.includes(kw)) { isExclude = true; break; }
                    }
                    if (isExclude) return;
                    // 排除作者信息卡片（粉丝、获赞）
                    if (commentText.includes('粉丝') && commentText.includes('获赞')) return;

                    const key = author + '|' + commentText;
                    if (!seen.has(key)) {
                        seen.add(key);
                        results.push({
                            text: commentText,
                            author: author,
                            digg_count: '0',
                            create_time: '',
                        });
                    }
                });

                return results.slice(0, maxCount);
            }''', max_count)

            print(f"[爬虫] 获取到 {len(comments)} 条评论")
            return comments
        except Exception as e:
            print(f"[爬虫] 评论获取失败: {e}")
            return []
        finally:
            page.close()

    # ==================== 上下文管理器支持 ====================

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
