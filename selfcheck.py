"""
启动自检
========
启动时检查运行环境是否就绪：关键依赖、抖音 cookies、飞书配置、数据目录与 SQLite。
缺失项给出明确的修复提示，避免服务跑起来后在运行中才报错。

用法：
    import selfcheck
    report = selfcheck.run_checks()
    selfcheck.print_report(report)
"""
import importlib
import json
import os
import sys

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
COOKIES_FILE = os.path.join(os.path.dirname(__file__), "cookies", "douyin_cookies.json")

REQUIRED_DEPS = ("playwright", "apscheduler", "fastapi", "uvicorn", "requests")

# 飞书配置必填字段（按监控启用与否区分）
LARK_REQUIRED_FIELDS = ("lark_app_id", "lark_app_secret", "lark_spreadsheet_token")
LARK_WEBHOOK_FIELD = "lark_webhook"


def run_checks() -> dict:
    """执行全部自检，返回结构化报告。"""
    report = {"ok": True, "checks": {}, "fix_hints": []}

    # 1) 关键依赖
    missing_deps = []
    for mod in REQUIRED_DEPS:
        try:
            importlib.import_module(mod)
            report["checks"][f"依赖 {mod}"] = "OK"
        except ImportError:
            missing_deps.append(mod)
            report["checks"][f"依赖 {mod}"] = "缺失"
    if missing_deps:
        report["ok"] = False
        report["fix_hints"].append(
            f"缺少依赖: {', '.join(missing_deps)}。请运行: pip install -r requirements.txt"
        )

    # 2) 抖音登录态
    if os.path.exists(COOKIES_FILE):
        try:
            with open(COOKIES_FILE, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            names = {c.get("name") for c in cookies if c.get("name")}
            has_session = bool({"sessionid", "sessionid_ss", "sid_tt"} & names)
            report["checks"]["抖音 cookies"] = (
                f"存在（{len(cookies)} 条）"
                + ("，含登录凭证" if has_session else "，但未发现登录凭证字段")
            )
            if not has_session:
                report["ok"] = False
                report["fix_hints"].append("抖音 cookies 缺少登录凭证，请在页面执行「登录」后重新扫码。")
        except (json.JSONDecodeError, OSError) as e:
            report["checks"]["抖音 cookies"] = f"文件损坏: {e}"
            report["ok"] = False
            report["fix_hints"].append("cookies 文件无法解析，请重新登录生成。")
    else:
        report["checks"]["抖音 cookies"] = "不存在"
        report["ok"] = False
        report["fix_hints"].append("未找到 cookies/douyin_cookies.json，请先登录抖音获取登录态。")

    # 3) 飞书配置
    config_path = os.path.join(DATA_DIR, "monitor_config.json")
    config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            report["checks"]["飞书配置"] = f"monitor_config.json 损坏: {e}"
            report["ok"] = False
    missing_lark = [f for f in LARK_REQUIRED_FIELDS if not config.get(f)]
    if config.get("lark_enabled"):
        if missing_lark:
            report["checks"]["飞书配置"] = f"已启用但缺少字段: {', '.join(missing_lark)}"
            report["ok"] = False
            report["fix_hints"].append("飞书同步已启用，请在「监控配置」中补齐 app_id / app_secret / spreadsheet_token。")
        else:
            report["checks"]["飞书配置"] = "已启用，凭据齐全"
    else:
        report["checks"]["飞书配置"] = "未启用（表格同步关闭）"
    if not config.get(LARK_WEBHOOK_FIELD):
        report["checks"]["飞书告警 webhook"] = "未配置（告警推送不可用）"
        report["fix_hints"].append("未配置 lark_webhook，点赞/评论告警将不会推送到飞书群。")
    else:
        report["checks"]["飞书告警 webhook"] = "已配置"

    # 4) 数据目录可写 + SQLite 可用
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        probe = os.path.join(DATA_DIR, ".write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        report["checks"]["数据目录"] = "可写"
    except OSError as e:
        report["checks"]["数据目录"] = f"不可写: {e}"
        report["ok"] = False
        report["fix_hints"].append("data/ 目录不可写，请检查磁盘权限。")

    try:
        import storage
        conn = storage._get_conn()
        conn.execute("SELECT 1")
        report["checks"]["SQLite 存储"] = "正常"
    except Exception as e:
        report["checks"]["SQLite 存储"] = f"异常: {e}"
        report["ok"] = False
        report["fix_hints"].append("SQLite 初始化失败，可删除 data/monitor.db 后重启（会自动重建）。")

    return report


def print_report(report: dict):
    """把自检结果打印到控制台（启动日志）。"""
    print("=" * 46)
    print("  启动自检")
    print("=" * 46)
    for name, result in report["checks"].items():
        mark = "✓" if "缺失" not in result and "异常" not in result and "不存在" not in result \
               and "损坏" not in result and "失败" not in result else "✗"
        print(f"  {mark} {name}: {result}")
    for hint in report["fix_hints"]:
        print(f"  ! 提示: {hint}")
    print("=" * 46)
    print("  自检" + ("通过，服务可正常运行" if report["ok"] else "发现问题，请按提示修复后再使用完整功能"))
    print("=" * 46)
