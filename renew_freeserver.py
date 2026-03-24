import os
import platform
import time
import traceback
from urllib.parse import unquote, urlparse
from typing import Optional, List

import requests
from seleniumbase import SB
from pyvirtualdisplay import Display


# ===== 固定地址，直接写死 =====
FREESERVER_MANAGE_URL = "https://dash.freeserver.tw/dashboard/manage/10829"
FREESERVER_DASHBOARD_URL = "https://dash.freeserver.tw/dashboard"
FREESERVER_LOGIN_URL = "https://dash.freeserver.tw/auth/login"

# ===== 环境变量 =====
# 这里只填 connect.sid 的 value，不要带 "connect.sid="
FREESERVER_COOKIE = (os.getenv("FREESERVER_COOKIE") or "").strip()

# 可选 TG 通知
TG_BOT_TOKEN = (os.getenv("TG_BOT_TOKEN") or "").strip()
TG_CHAT_ID = (os.getenv("TG_CHAT_ID") or "").strip()

# 本地 Gost 转发后的代理地址
# GitHub Actions 中会传入：127.0.0.1:8080
LOCAL_HTTP_PROXY = (os.getenv("LOCAL_HTTP_PROXY") or "127.0.0.1:8080").strip()

SCREENSHOT_DIR = "screenshots"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

WAIT_TIMEOUT = 25

COOKIE_NAME = "connect.sid"

SWAL_CONFIRM_SELECTORS = [
    'button.swal2-confirm',
    'xpath=//button[contains(@class, "swal2-confirm")]',
]

EXTEND_BUTTON_SELECTORS = [
    'button.text-green-700',
    'xpath=//button[contains(normalize-space(.), "延长到期时间")]',
    'xpath=//button[contains(normalize-space(.), "延長到期時間")]',
    'xpath=//button[contains(normalize-space(.), "延长到期日")]',
    'xpath=//button[contains(normalize-space(.), "延長到期日")]',
    'xpath=//button[contains(normalize-space(.), "Extend")]',
]


def setup_xvfb():
    if platform.system().lower() == "linux" and not os.environ.get("DISPLAY"):
        display = Display(visible=False, size=(1600, 1200))
        display.start()
        os.environ["DISPLAY"] = display.new_display_var
        print("🖥️ Xvfb 已启动")
        return display
    return None


def screenshot(sb: SB, name: str) -> str:
    path = os.path.join(SCREENSHOT_DIR, name)
    sb.save_screenshot(path)
    print(f"📸 {path}")
    return path


def tg_send_text(text: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            json={
                "chat_id": TG_CHAT_ID,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=20,
        ).raise_for_status()
    except Exception as e:
        print(f"⚠️ TG 文本发送失败：{e}")


def tg_send_photo(photo_path: str, caption: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return

    if not photo_path or not os.path.exists(photo_path):
        print(f"⚠️ TG 图片不存在：{photo_path}")
        tg_send_text(caption)
        return

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, "rb") as f:
            requests.post(
                url,
                data={
                    "chat_id": TG_CHAT_ID,
                    "caption": caption[:1024],
                    "disable_notification": False,
                },
                files={"photo": f},
                timeout=60,
            ).raise_for_status()
    except Exception as e:
        print(f"⚠️ TG 图片发送失败：{e}")
        tg_send_text(caption)


def get_cookie_candidates(raw_cookie_value: str) -> List[str]:
    """
    FREESERVER_COOKIE 只填 cookie 的 value，不带 connect.sid=
    例如：
    s%3AU7SDOqq5TGZbUevDjMHp89nJILa7wB00.CeXEU0slnhYDwaWEzsX%2Fr6S7yrBIumOv2e4%2FU48gTyo
    """
    if not raw_cookie_value:
        raise RuntimeError("❌ FREESERVER_COOKIE 为空")

    value = raw_cookie_value.strip()
    if not value:
        raise RuntimeError("❌ FREESERVER_COOKIE 为空白")

    candidates: List[str] = []
    decoded = unquote(value)

    # 优先尝试解码后的值，再尝试原始值
    if decoded and decoded not in candidates:
        candidates.append(decoded)
    if value and value not in candidates:
        candidates.append(value)

    return candidates


def page_looks_like_login(sb: SB) -> bool:
    try:
        current_url = (sb.get_current_url() or "").strip().lower()
    except Exception:
        current_url = ""

    if "/auth/login" in current_url:
        return True

    try:
        page_text = (sb.get_text("body") or "").strip().lower()
    except Exception:
        page_text = ""

    login_hints = [
        "使用 discord 登入",
        "登入",
        "login",
        "discord",
    ]

    return any(hint in page_text for hint in login_hints)


def open_base_for_cookie(sb: SB):
    parsed = urlparse(FREESERVER_LOGIN_URL)
    base_url = f"{parsed.scheme}://{parsed.netloc}/"
    sb.open(base_url)
    sb.wait_for_element_visible("body", timeout=WAIT_TIMEOUT)
    time.sleep(1)


def try_inject_cookie_and_login(sb: SB) -> bool:
    candidates = get_cookie_candidates(FREESERVER_COOKIE)
    domain = urlparse(FREESERVER_DASHBOARD_URL).netloc

    for idx, candidate_value in enumerate(candidates, start=1):
        print(f"🍪 尝试注入 Cookie（方案 {idx}/{len(candidates)}）")

        try:
            sb.driver.delete_all_cookies()
        except Exception:
            pass

        open_base_for_cookie(sb)

        cookie_obj = {
            "name": COOKIE_NAME,
            "value": candidate_value,
            "domain": domain,
            "path": "/",
            "secure": True,
        }

        try:
            sb.driver.add_cookie(cookie_obj)
        except Exception as e:
            print(f"⚠️ 注入 Cookie 失败：{e}")
            continue

        sb.open(FREESERVER_DASHBOARD_URL)
        sb.wait_for_element_visible("body", timeout=WAIT_TIMEOUT)
        time.sleep(2)

        if not page_looks_like_login(sb):
            print("✅ Cookie 登录成功")
            return True

        print("⚠️ 仍然停留在登录页，继续尝试下一个 Cookie 方案")

    return False


def wait_and_click_any(sb: SB, selectors, label: str, timeout_each: int = 10):
    last_error = None

    for sel in selectors:
        try:
            sb.wait_for_element_present(sel, timeout=timeout_each)
            try:
                sb.scroll_to(sel)
            except Exception:
                pass
            time.sleep(0.5)

            try:
                sb.click(sel)
            except Exception:
                sb.js_click(sel)

            print(f"✅ 已点击 {label}: {sel}")
            return
        except Exception as e:
            last_error = e

    raise RuntimeError(f"❌ 未找到 {label}，最后错误：{last_error}")


def try_click_optional(sb: SB, selectors, label: str, timeout_each: int = 8) -> bool:
    for sel in selectors:
        try:
            sb.wait_for_element_present(sel, timeout=timeout_each)
            try:
                sb.scroll_to(sel)
            except Exception:
                pass
            time.sleep(0.5)

            try:
                sb.click(sel)
            except Exception:
                sb.js_click(sel)

            print(f"✅ 已点击 {label}: {sel}")
            return True
        except Exception:
            continue

    print(f"ℹ️ 未检测到 {label}")
    return False


def run_renew_flow(sb: SB) -> str:
    sb.open(FREESERVER_MANAGE_URL)
    sb.wait_for_element_visible("body", timeout=WAIT_TIMEOUT)
    time.sleep(2)

    if page_looks_like_login(sb):
        raise RuntimeError("❌ 打开管理页后仍跳到登录页，Cookie 可能已失效")

    before_shot = screenshot(sb, f"before_{int(time.time())}.png")

    wait_and_click_any(sb, EXTEND_BUTTON_SELECTORS, "延长到期时间按钮", timeout_each=12)

    time.sleep(1.5)
    wait_and_click_any(sb, SWAL_CONFIRM_SELECTORS, "第一次确认按钮", timeout_each=12)

    time.sleep(2)
    try_click_optional(sb, SWAL_CONFIRM_SELECTORS, "成功提示 OK", timeout_each=10)

    time.sleep(1.5)
    after_shot = screenshot(sb, f"after_{int(time.time())}.png")

    print(f"📸 执行前截图：{before_shot}")
    print(f"📸 执行后截图：{after_shot}")

    return after_shot


def main():
    if not FREESERVER_COOKIE:
        raise RuntimeError("❌ 缺少环境变量：FREESERVER_COOKIE")

    display = setup_xvfb()
    result_shot: Optional[str] = None

    try:
        with SB(
            uc=True,
            locale="zh",
            test=True,
            proxy=LOCAL_HTTP_PROXY,
        ) as sb:
            print("🚀 浏览器启动（UC Mode）")
            print(f"🌐 浏览器代理：{LOCAL_HTTP_PROXY}")

            ok = try_inject_cookie_and_login(sb)
            if not ok:
                raise RuntimeError("❌ Cookie 登录失败，请更新 FREESERVER_COOKIE")

            result_shot = run_renew_flow(sb)

        msg = (
            "✅ FreeServer 自动延长完成\n"
            f"管理页：{FREESERVER_MANAGE_URL}\n"
            f"登录页：{FREESERVER_LOGIN_URL}\n"
            f"Dashboard：{FREESERVER_DASHBOARD_URL}"
        )
        print(msg)

        if result_shot:
            tg_send_photo(result_shot, msg)
        else:
            tg_send_text(msg)

    except Exception as e:
        err = (
            "❌ FreeServer 自动延长失败\n"
            f"错误：{e}\n\n"
            f"{traceback.format_exc()}"
        )
        print(err)
        tg_send_text(err)
        raise
    finally:
        if display:
            display.stop()


if __name__ == "__main__":
    main()
