"""
自动登录管理器
================
未登录时启动有头浏览器，用户扫码登录后自动提取 cookies 并保存。
全程无需手动导出 cookies。

使用流程：
1. 调用 start_login() 启动有头浏览器，打开抖音登录页
2. 前端轮询 get_login_status()
3. 用户在弹出的浏览器中扫码登录
4. 检测到登录成功后，自动提取 cookies 保存到文件
5. 关闭浏览器，状态变为 success
"""
import json
import os
import time
import threading
from typing import Optional
from playwright.sync_api import sync_playwright
import storage


class LoginSession:
    """单次登录会话"""

    def __init__(self, cookies_file: str):
        self.cookies_file = cookies_file
        self.status = "pending"  # pending / success / failed / cancelled
        self.message = "等待扫码登录..."
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self):
        """在独立线程中启动有头浏览器并等待登录"""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        """核心登录流程（在独立线程运行）"""
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=False,  # 有头模式，用户可见
                channel="chrome",
                args=[
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--start-maximized',
                ]
            )
            self._context = self._browser.new_context(
                viewport={'width': 1280, 'height': 800},
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                           'AppleWebKit/537.36 (KHTML, like Gecko) '
                           'Chrome/120.0.0.0 Safari/537.36'
            )
            self._page = self._context.new_page()

            # 打开抖音首页，会自动显示登录弹窗
            with self._lock:
                self.status = "pending"
                self.message = "请在弹出的浏览器中扫码登录..."

            self._page.goto('https://www.douyin.com/', wait_until='domcontentloaded', timeout=30000)
            self._page.wait_for_timeout(2000)

            # 轮询检测登录状态，最多等待 5 分钟
            max_wait = 300  # 5分钟
            start = time.time()
            while time.time() - start < max_wait:
                if self.status == "cancelled":
                    return
                logged_in = self._check_logged_in()
                if logged_in:
                    self._handle_login_success()
                    return
                time.sleep(2)

            with self._lock:
                self.status = "failed"
                self.message = "登录超时，请重试"
        except Exception as e:
            with self._lock:
                self.status = "failed"
                self.message = f"登录出错: {e}"
        finally:
            self._cleanup()

    def _check_logged_in(self) -> bool:
        """检测页面是否已登录"""
        if not self._page:
            return False
        try:
            return self._page.evaluate('''() => {
                // 检测是否有登录用户头像
                const avatar = document.querySelector('[data-e2e="top-avatar"], img[class*="avatar-component"]');
                if (avatar && avatar.src && avatar.src.includes('douyinpic')) return true;
                // 检测是否有登录按钮（有则未登录）
                const loginBtn = document.querySelector('[data-e2e="login-button"], button[data-e2e*="login"]');
                if (loginBtn) return false;
                // 检测页面文本
                const text = document.body.innerText.substring(0, 1000);
                if (text.includes('扫码登录') || text.includes('登录后更精彩')) return false;
                // 如果有用户头像区域且没有登录按钮，认为已登录
                return !!avatar;
            }''')
        except Exception:
            return False

    def _handle_login_success(self):
        """登录成功后提取 cookies 并保存"""
        try:
            with self._lock:
                self.status = "saving"
                self.message = "登录成功，正在保存 cookies..."

            # 等待页面完全加载，确保所有 cookies 都已设置
            self._page.wait_for_timeout(3000)

            # 提取所有 cookies
            cookies = self._context.cookies()

            # 转换为标准格式（与 EditThisCookie 导出格式兼容）
            saved_cookies = []
            for i, c in enumerate(cookies):
                saved_cookies.append({
                    "domain": c.get('domain', ''),
                    "expirationDate": c.get('expires', -1),
                    "hostOnly": False,
                    "httpOnly": c.get('httpOnly', False),
                    "name": c.get('name', ''),
                    "path": c.get('path', '/'),
                    "sameSite": "unspecified",
                    "secure": c.get('secure', False),
                    "session": c.get('expires', -1) == -1,
                    "storeId": "0",
                    "value": c.get('value', ''),
                    "id": i + 1,
                })

            # 保存到文件
            os.makedirs(os.path.dirname(self.cookies_file), exist_ok=True)
            with open(self.cookies_file, 'w', encoding='utf-8') as f:
                json.dump(saved_cookies, f, ensure_ascii=False, indent=2)

            # 提取当前登录用户的头像和昵称
            try:
                profile = self._extract_user_profile()
                if profile:
                    storage.save_user_profile(
                        nickname=profile.get('nickname', ''),
                        avatar=profile.get('avatar', '')
                    )
            except Exception as e:
                print(f"[登录] 提取用户资料失败: {e}")

            with self._lock:
                self.status = "success"
                self.message = f"登录成功，已保存 {len(saved_cookies)} 条 cookies"
            print(f"[登录] 成功，保存 {len(saved_cookies)} 条 cookies 到 {self.cookies_file}")

        except Exception as e:
            with self._lock:
                self.status = "failed"
                self.message = f"保存 cookies 失败: {e}"

    def _extract_user_profile(self) -> dict:
        """从当前页面提取登录用户的头像和昵称"""
        if not self._page:
            return {}
        try:
            return self._page.evaluate('''() => {
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
                if (!nickname) {
                    const avatarContainers = document.querySelectorAll('[data-e2e*="avatar"], [class*="avatar-container"]');
                    for (const container of avatarContainers) {
                        const text = (container.textContent || '').trim();
                        if (text && text.length < 30) { nickname = text; break; }
                    }
                }
                return { nickname, avatar };
            }''')
        except Exception:
            return {}

    def _cleanup(self):
        """清理浏览器资源"""
        try:
            if self._page:
                self._page.close()
        except Exception:
            pass
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass

    def cancel(self):
        """取消登录"""
        with self._lock:
            self.status = "cancelled"
            self.message = "已取消登录"

    def get_status(self) -> dict:
        with self._lock:
            return {
                "status": self.status,
                "message": self.message,
            }


# 全局登录会话（单例）
_current_session: Optional[LoginSession] = None


def start_login(cookies_file: str) -> dict:
    """启动登录流程"""
    global _current_session
    if _current_session and _current_session.status in ("pending", "saving"):
        return {"success": False, "message": "已有登录流程进行中"}
    _current_session = LoginSession(cookies_file)
    _current_session.start()
    return {"success": True, "message": "登录浏览器已启动，请扫码"}


def get_login_status() -> dict:
    """获取当前登录状态"""
    global _current_session
    if not _current_session:
        return {"status": "idle", "message": "无登录会话"}
    return _current_session.get_status()


def cancel_login() -> dict:
    """取消登录"""
    global _current_session
    if _current_session:
        _current_session.cancel()
    return {"success": True, "message": "已取消"}
