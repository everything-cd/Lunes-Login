import os
import platform
import time
import traceback
from urllib.parse import unquote, urlparse
from typing import Optional, Tuple, List

import requests
from seleniumbase import SB
from pyvirtualdisplay import Display


FREESERVER_MANAGE_URL = os.getenv(
    "FREESERVER_MANAGE_URL",
    "https://dash.freeserver.tw/dashboard/manage/10829",
).strip()

FREESERVER_DASHBOARD_URL = os.getenv(
    "FREESERVER_DASHBOARD_URL",
    "https://dash.freeserver.tw/dashboard",
).strip()

FREESERVER_LOGIN_URL = os.getenv(
    "FREESERVER_LOGIN_URL",
    "https://dash.freeserver.tw/auth/login",
).strip()

FREESERVER_COOKIE = (os.getenv("FREESERVER_COOKIE") or "").strip()

SCREENSHOT_DIR = "screenshots"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

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

WAIT_TIMEOUT = 25


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
    token = (os.getenv("TG_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TG_CHAT_ID") or "").strip()
    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=20,
        ).raise_for_status()
    except Exception as e:
        print(f"⚠️ TG 文本发送失败：{e}")


def tg_send_photo(photo_path: str, caption: str):
    token = (os.getenv("TG_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TG_CHAT_ID") or "").strip()
    if not token or not chat_id:
        return

    if not photo_path or not os.path.exists(photo_path):
        print(f"⚠️ TG 图片不存在：{photo_path}")
        tg_send_text(caption)
        return

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    try:
        with open(photo_path, "rb") as f:
            requests.post(
                url,
                data={
                    "chat_id": chat_id,
                    "caption": caption[:1024],
                    "disable_notification": False,
                },
                files={"photo": f},
                timeout=60,
            ).raise_for_status()
    except Exception as e:
        print(f"⚠️ TG 图片发送失败：{e}")
        tg_send_text(caption)


def parse_cookie_string(raw_cookie: str) -> Tuple[str, str]:
    """
    只接受这种格式：
    connect.sid=xxxx
    """
    if not raw_cookie or "=" not in raw_cookie:
        raise RuntimeError("❌ FREESERVER_COOKIE 格式错误，必须是 name=value")

    name, value = raw_cookie.split("=", 1)
    name = name.strip()
    value = value.strip()

    if not name or not value:
        raise RuntimeError("❌ FREESERVER_COOKIE 存在空的 name 或 value")

    return name, value


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
    cookie_name, cookie_value_raw = parse_cookie_string(FREESERVER_COOKIE)

    candidates: List[str] = []
    decoded = unquote(cookie_value_raw)

    # 先试解码后的值，再试原始值
    if decoded and decoded not in candidates:
        candidates.append(decoded)
    if cookie_value_raw and cookie_value_raw not in candidates:
        candidates.append(cookie_value_raw)

    domain = urlparse(FREESERVER_DASHBOARD_URL).netloc

    for idx, candidate_value in enumerate(candidates, start=1):
        print(f"🍪 尝试注入 Cookie（方案 {idx}/{len(candidates)}）")

        try:
            sb.driver.delete_all_cookies()
        except Exception:
            pass

        open_base_for_cookie(sb)

        cookie_obj = {
            "name": cookie_name,
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
        with SB(uc=True, locale="zh", test=True) as sb:
            print("🚀 浏览器启动（UC Mode）")

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
