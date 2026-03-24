import os
import platform
import time
import traceback
import subprocess
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
# 例如：127.0.0.1:8080
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

EXPAND_POPUP_JS = """
(function() {
    var turnstileInput = document.querySelector('input[name="cf-turnstile-response"]');
    if (!turnstileInput) return;
    var el = turnstileInput;
    for (var i = 0; i < 20; i++) {
        el = el.parentElement;
        if (!el) break;
        var style = window.getComputedStyle(el);
        if (style.overflow === 'hidden' || style.overflowX === 'hidden' || style.overflowY === 'hidden') {
            el.style.overflow = 'visible';
        }
        el.style.minWidth = 'max-content';
    }
    var iframes = document.querySelectorAll('iframe');
    iframes.forEach(function(iframe) {
        if (iframe.src && iframe.src.includes('challenges.cloudflare.com')) {
            iframe.style.width = '300px';
            iframe.style.height = '65px';
            iframe.style.minWidth = '300px';
            iframe.style.visibility = 'visible';
            iframe.style.opacity = '1';
        }
    });
})();
"""


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


def get_requests_proxies():
    if not LOCAL_HTTP_PROXY:
        return None

    proxy_url = LOCAL_HTTP_PROXY
    if not proxy_url.startswith("http://") and not proxy_url.startswith("https://"):
        proxy_url = f"http://{proxy_url}"

    return {
        "http": proxy_url,
        "https": proxy_url,
    }


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
            proxies=get_requests_proxies(),
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
                proxies=get_requests_proxies(),
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
        "sign in",
    ]

    return any(hint in page_text for hint in login_hints)


def open_base_for_cookie(sb: SB):
    parsed = urlparse(FREESERVER_LOGIN_URL)
    base_url = f"{parsed.scheme}://{parsed.netloc}/"
    sb.uc_open_with_reconnect(base_url, reconnect_time=3)
    sb.wait_for_element_visible("body", timeout=WAIT_TIMEOUT)
    time.sleep(1)


def xdotool_click(x, y):
    x, y = int(x), int(y)
    try:
        result = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--class", "chrome"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        wids = [w for w in result.stdout.strip().split("\n") if w]
        if wids:
            subprocess.run(
                ["xdotool", "windowactivate", wids[-1]],
                timeout=2,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.2)

        subprocess.run(["xdotool", "mousemove", str(x), str(y)], timeout=2, check=True)
        time.sleep(0.15)
        subprocess.run(["xdotool", "click", "1"], timeout=2, check=True)
        print(f"📐 坐标点击成功: ({x}, {y})")
        return True
    except Exception as e:
        print(f"⚠️ xdotool 点击失败：{e}")
        return False


def get_turnstile_coords(sb):
    try:
        return sb.execute_script("""
            (function(){
                var iframes = document.querySelectorAll('iframe');
                for (var i = 0; i < iframes.length; i++) {
                    var src = iframes[i].src || '';
                    if (src.includes('cloudflare') || src.includes('turnstile')) {
                        var rect = iframes[i].getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            return {
                                click_x: Math.round(rect.x + 30),
                                click_y: Math.round(rect.y + rect.height / 2)
                            };
                        }
                    }
                }

                var input = document.querySelector('input[name="cf-turnstile-response"]');
                if (input) {
                    var container = input.parentElement;
                    for (var j = 0; j < 5; j++) {
                        if (!container) break;
                        var rect = container.getBoundingClientRect();
                        if (rect.width > 100 && rect.height > 30) {
                            return {
                                click_x: Math.round(rect.x + 30),
                                click_y: Math.round(rect.y + rect.height / 2)
                            };
                        }
                        container = container.parentElement;
                    }
                }
                return null;
            })()
        """)
    except Exception:
        return None


def get_window_offset(sb):
    try:
        result = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--class", "chrome"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        wids = [w for w in result.stdout.strip().split("\n") if w]
        if wids:
            geo = subprocess.run(
                ["xdotool", "getwindowgeometry", "--shell", wids[-1]],
                capture_output=True,
                text=True,
                timeout=3,
            ).stdout
            geo_dict = {}
            for line in geo.strip().split("\n"):
                if "=" in line:
                    k, v = line.split("=", 1)
                    geo_dict[k.strip()] = int(v.strip())

            win_x = geo_dict.get("X", 0)
            win_y = geo_dict.get("Y", 0)

            info = sb.execute_script("""
                (function(){
                    return {
                        outer: window.outerHeight,
                        inner: window.innerHeight
                    };
                })()
            """)
            toolbar = info["outer"] - info["inner"]
            if not (30 <= toolbar <= 200):
                toolbar = 87
            return win_x, win_y, toolbar
    except Exception:
        pass

    try:
        info = sb.execute_script("""
            (function(){
                return {
                    screenX: window.screenX || 0,
                    screenY: window.screenY || 0,
                    outer: window.outerHeight,
                    inner: window.innerHeight
                };
            })()
        """)
        toolbar = info["outer"] - info["inner"]
        if not (30 <= toolbar <= 200):
            toolbar = 87
        return info["screenX"], info["screenY"], toolbar
    except Exception:
        return 0, 0, 87


def turnstile_exists(sb) -> bool:
    try:
        return sb.execute_script("""
            (function(){
                return (
                    document.querySelector('input[name="cf-turnstile-response"]') !== null ||
                    Array.from(document.querySelectorAll('iframe')).some(
                        f => (f.src || '').includes('cloudflare') || (f.src || '').includes('turnstile')
                    )
                );
            })()
        """)
    except Exception:
        return False


def check_turnstile_token(sb) -> bool:
    try:
        return sb.execute_script("""
            (function(){
                var input = document.querySelector('input[name="cf-turnstile-response"]');
                return !!(input && input.value && input.value.length > 20);
            })()
        """)
    except Exception:
        return False


def solve_turnstile(sb) -> bool:
    for _ in range(3):
        try:
            sb.execute_script(EXPAND_POPUP_JS)
        except Exception:
            pass
        time.sleep(0.5)

    if check_turnstile_token(sb):
        print("✅ Turnstile Token 已存在")
        return True

    coords = get_turnstile_coords(sb)
    if not coords:
        print("❌ 无法获取 Turnstile 坐标")
        return False

    win_x, win_y, toolbar = get_window_offset(sb)
    abs_x = coords["click_x"] + win_x
    abs_y = coords["click_y"] + win_y + toolbar

    print(f"🖱️ 点击 Turnstile: ({abs_x}, {abs_y})")
    if not xdotool_click(abs_x, abs_y):
        return False

    for _ in range(30):
        time.sleep(0.5)
        if check_turnstile_token(sb):
            print("✅ Cloudflare Turnstile 通过")
            return True

    print("❌ Cloudflare Turnstile 超时")
    return False


def prepare_cloudflare_session(sb):
    print("🛡️ 先访问登录页，准备获取 Cloudflare clearance...")
    sb.uc_open_with_reconnect(FREESERVER_LOGIN_URL, reconnect_time=4)
    time.sleep(3)

    for _ in range(20):
        if turnstile_exists(sb):
            print("🛡️ 检测到 Turnstile")
            if not solve_turnstile(sb):
                screenshot(sb, f"cf_failed_{int(time.time())}.png")
                raise RuntimeError("❌ Cloudflare Turnstile 验证失败")
            time.sleep(2)
            break
        time.sleep(0.5)
    else:
        print("ℹ️ 未检测到 Turnstile，继续")

    try:
        cf_cookie = sb.driver.get_cookie("cf_clearance")
        if cf_cookie:
            print("✅ 已获得 cf_clearance")
        else:
            print("ℹ️ 当前未读到 cf_clearance，继续尝试后续流程")
    except Exception:
        pass


def try_inject_cookie_and_login(sb) -> bool:
    candidates = get_cookie_candidates(FREESERVER_COOKIE)
    domain = urlparse(FREESERVER_DASHBOARD_URL).netloc

    # 先让浏览器自己过 Cloudflare，保留 cf_clearance
    prepare_cloudflare_session(sb)

    for idx, candidate_value in enumerate(candidates, start=1):
        print(f"🍪 尝试注入 Cookie（方案 {idx}/{len(candidates)}）")

        # 只删除 connect.sid，不要 delete_all_cookies()
        try:
            sb.driver.delete_cookie(COOKIE_NAME)
        except Exception:
            pass

        open_base_for_cookie(sb)

        cookie_obj = {
            "name": COOKIE_NAME,
            "value": candidate_value,
            "domain": domain,
            "path": "/",
            "secure": True,
            "httpOnly": True,
        }

        try:
            sb.driver.add_cookie(cookie_obj)
            print("✅ connect.sid 已注入")
        except Exception as e:
            print(f"⚠️ 注入 Cookie 失败：{e}")
            continue

        sb.open(FREESERVER_DASHBOARD_URL)
        sb.wait_for_element_visible("body", timeout=WAIT_TIMEOUT)
        time.sleep(3)

        current_url = ""
        try:
            current_url = sb.get_current_url()
        except Exception:
            pass
        print(f"🌐 当前URL: {current_url}")

        if not page_looks_like_login(sb):
            print("✅ Cookie 登录成功")
            return True

        screenshot(sb, f"login_retry_{idx}_{int(time.time())}.png")
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
    sb = None

    try:
        with SB(
            uc=True,
            locale="zh",
            proxy=LOCAL_HTTP_PROXY,
        ) as sb:
            print("🚀 浏览器启动（UC Mode）")
            print(f"🌐 浏览器代理：{LOCAL_HTTP_PROXY}")

            ok = try_inject_cookie_and_login(sb)
            if not ok:
                fail_shot = screenshot(sb, f"login_failed_{int(time.time())}.png")
                raise RuntimeError(
                    f"❌ Cookie 登录失败，请更新 FREESERVER_COOKIE\n失败截图：{fail_shot}"
                )

            result_shot = run_renew_flow(sb)

        msg = (
            "✅ FreeServer 自动延长完成\n"
            f"管理页：{FREESERVER_MANAGE_URL}\n"
            f"登录页：{FREESERVER_LOGIN_URL}\n"
            f"Dashboard：{FREESERVER_DASHBOARD_URL}"
        )
        print(msg)

        if result_shot and os.path.exists(result_shot):
            tg_send_photo(result_shot, msg)
        else:
            tg_send_text(msg)

    except Exception as e:
        fail_shot = None

        try:
            if sb:
                fail_shot = screenshot(sb, f"error_{int(time.time())}.png")
        except Exception as shot_err:
            print(f"⚠️ 保存异常截图失败：{shot_err}")

        err = (
            "❌ FreeServer 自动延长失败\n"
            f"错误：{e}\n"
            + (f"失败截图：{fail_shot}\n\n" if fail_shot else "\n")
            + traceback.format_exc()
        )
        print(err)

        if fail_shot and os.path.exists(fail_shot):
            tg_send_photo(fail_shot, err[:1024])
        else:
            tg_send_text(err)

        raise
    finally:
        if display:
            display.stop()


if __name__ == "__main__":
    main()
