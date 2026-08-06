import os
import platform
import time
import random
import re
from typing import List, Dict, Optional, Tuple

import requests
from seleniumbase import SB
from pyvirtualdisplay import Display

LOGIN_URL = "https://betadash.lunes.host/login?next=/"
SERVER_URL_TPL = "https://betadash.lunes.host/servers/{server_id}"

SCREENSHOT_DIR = "screenshots"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# 登录表单选择器
EMAIL_SEL = "#email"
PASS_SEL = "#password"
SUBMIT_SEL = 'button.submit-btn[type="submit"]'

# 登录成功特征
LOGOUT_SEL = 'a[href="/logout"].action-btn.ghost'

# server 页标志
NOW_MANAGING_XPATH = 'xpath=//p[contains(normalize-space(.), "Now managing")]'

# 服务器卡片
SERVER_CARD_LINK_SEL = 'a.server-card[href^="/servers/"]'


def mask_email_keep_domain(email: str) -> str:
    e = (email or "").strip()
    if "@" not in e:
        return "***"
    name, domain = e.split("@", 1)
    if len(name) <= 1:
        name_mask = name or "*"
    elif len(name) == 2:
        name_mask = name[0] + name[1]
    else:
        name_mask = name[0] + ("*" * (len(name) - 2)) + name[-1]
    return f"{name_mask}@{domain}"


def safe_filename(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"[^a-zA-Z0-9._-]+", "_", text)
    return text[:120] if text else f"shot_{int(time.time())}"


def setup_xvfb():
    if platform.system().lower() == "linux" and not os.environ.get("DISPLAY"):
        display = Display(visible=False, size=(1920, 1080))
        display.start()
        os.environ["DISPLAY"] = display.new_display_var
        print("🖥️ Xvfb 已启动")
        return display
    return None


def screenshot(sb, name: str) -> str:
    path = os.path.join(SCREENSHOT_DIR, name)
    sb.save_screenshot(path)
    print(f"📸 {path}")
    return path


def tg_send_text(text: str, token: Optional[str] = None, chat_id: Optional[str] = None):
    token = (token or "").strip()
    chat_id = (chat_id or "").strip()
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


def tg_send_photo(
    photo_path: str,
    caption: str,
    token: Optional[str] = None,
    chat_id: Optional[str] = None,
):
    token = (token or "").strip()
    chat_id = (chat_id or "").strip()
    if not token or not chat_id:
        return

    if not photo_path or not os.path.exists(photo_path):
        print(f"⚠️ TG 图片发送失败：文件不存在 -> {photo_path}")
        tg_send_text(caption, token, chat_id)
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
        tg_send_text(caption, token, chat_id)


def build_accounts_from_env() -> List[Dict[str, str]]:
    batch = (os.getenv("ACCOUNTS_BATCH") or "").strip()
    if not batch:
        raise RuntimeError("❌ 缺少环境变量：ACCOUNTS_BATCH")

    accounts: List[Dict[str, str]] = []
    for idx, raw in enumerate(batch.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        parts = [p.strip() for p in line.split(",")]

        if len(parts) not in (2, 4):
            raise RuntimeError(
                f"❌ ACCOUNTS_BATCH 第 {idx} 行格式不对：{raw!r}"
            )

        email, password = parts[0], parts[1]
        tg_token = parts[2] if len(parts) == 4 else ""
        tg_chat = parts[3] if len(parts) == 4 else ""

        if not email or not password:
            raise RuntimeError(f"❌ 第 {idx} 行空字段：{raw!r}")

        accounts.append(
            {
                "email": email,
                "password": password,
                "tg_token": tg_token,
                "tg_chat": tg_chat,
            }
        )

    if not accounts:
        raise RuntimeError("❌ 没有有效账号")

    return accounts


def _has_cf_clearance(sb: SB) -> bool:
    try:
        cookies = sb.get_cookies()
        cf_clearance = next((c["value"] for c in cookies if c.get("name") == "cf_clearance"), None)
        print("🧩 cf_clearance:", "OK" if cf_clearance else "NONE")
        return bool(cf_clearance)
    except Exception:
        return False


def _force_input_value(sb: SB, selector: str, value: str):
    sb.execute_script(
        """
        var el = document.querySelector(arguments[0]);
        if(el) {
            el.value = arguments[1];
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }
        """,
        selector,
        value,
    )


def _try_click_captcha(sb: SB, stage: str):
    try:
        sb.uc_gui_click_captcha()
        time.sleep(3)
        print(f"   ✅ captcha 点击完成（{stage}）")
    except Exception as e:
        print(f"   ⚠️ captcha 点击异常（{stage}）：{e}")


def _resolve_cf_challenge(sb: SB):
    print("🛡️ 正在解决 Cloudflare 挑战...")
    for i in range(5):
        if _has_cf_clearance(sb):
            print("   ✅ 已有 cf_clearance")
            return
        _try_click_captcha(sb, f"循环 {i+1}")
        time.sleep(2)
    print("   ⚠️ 多次尝试后仍未获得 cf_clearance")


def _detect_login_error(sb: SB) -> str:
    error_selectors = [
        ".alert-danger",
        ".toast-error",
        "#error",
        ".form-error",
        ".invalid-feedback",
        'div[role="alert"]',
    ]
    for sel in error_selectors:
        try:
            if sb.is_element_visible(sel):
                text = sb.get_text(sel)
                if text and text.strip():
                    return f"{sel}: {text.strip()}"
        except Exception:
            pass
    return ""


def _is_logged_in(sb: SB) -> Tuple[bool, Optional[str]]:
    try:
        if sb.is_element_visible("h1.hero-title"):
            welcome = sb.get_text("h1.hero-title").strip()
            if "welcome back" in welcome.lower():
                return True, welcome
    except Exception:
        pass
    try:
        if sb.is_element_visible(LOGOUT_SEL):
            return True, None
    except Exception:
        pass
    return False, None


def _extract_server_id_from_href(href: str) -> Optional[str]:
    if not href:
        return None
    m = re.search(r"/servers/(\d+)", href)
    return m.group(1) if m else None


def _find_server_id_and_go_server_page(sb: SB) -> Tuple[Optional[str], bool, str]:
    try:
        sb.wait_for_element_visible(SERVER_CARD_LINK_SEL, timeout=25)
    except Exception:
        screenshot(sb, f"server_card_not_found_{int(time.time())}.png")
        return None, False, "server-card 未出现"

    try:
        href = sb.get_attribute(SERVER_CARD_LINK_SEL, "href") or ""
    except Exception:
        href = ""

    server_id = _extract_server_id_from_href(href)
    if not server_id:
        screenshot(sb, f"server_id_extract_failed_{int(time.time())}.png")
        return None, False, "无法提取 server_id"

    server_url = SERVER_URL_TPL.format(server_id=server_id)

    try:
        print(f"🧭 提取到 server_id={server_id}，点击跳转...")
        sb.scroll_to(SERVER_CARD_LINK_SEL)
        time.sleep(0.3)
        sb.click(SERVER_CARD_LINK_SEL)
    except Exception as e:
        print(f"⚠️ 点击失败，直接打开：{server_url} | {e}")
        try:
            sb.open(server_url)
        except Exception as e2:
            screenshot(sb, f"goto_server_open_failed_{int(time.time())}.png")
            return server_id, False, f"打开失败: {e2}"

    for _ in range(30):
        try:
            cur = (sb.get_current_url() or "").strip()
        except:
            cur = ""

        if f"/servers/{server_id}" in cur:
            if sb.is_element_visible(NOW_MANAGING_XPATH):
                return server_id, True, "Now managing 已出现"
            if sb.is_element_visible("body"):
                return server_id, True, "URL 已进入 server 页"
        time.sleep(1)

    screenshot(sb, f"goto_server_failed_{int(time.time())}.png")
    return server_id, False, "30 秒内未识别 server 页"


def _post_login_visit(sb: SB) -> Tuple[Optional[str], bool, str]:
    server_id, ok, reason = _find_server_id_and_go_server_page(sb)
    if ok:
        stay = random.randint(4, 6)
        print(f"⏳ 停留 {stay} 秒...")
        time.sleep(stay)
    return server_id, ok, reason


def login_then_flow_one_account(
    email: str, password: str
) -> Tuple[str, Optional[str], bool, str, Optional[str], Optional[str], bool, str]:
    with SB(uc=True, locale="en", test=True) as sb:
        print("🚀 浏览器启动（UC Mode）")

        sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=5.0)
        time.sleep(3)

        # 解决 CF
        _resolve_cf_challenge(sb)

        # 等待表单
        try:
            sb.wait_for_element_visible(EMAIL_SEL, timeout=20)
            sb.wait_for_element_visible(PASS_SEL, timeout=10)
            sb.wait_for_element_visible(SUBMIT_SEL, timeout=10)
            print("✅ 登录表单已加载")
        except Exception:
            url_now = sb.get_current_url() or ""
            shot_path = screenshot(sb, f"FAIL_form_not_found_{safe_filename(email)}_{int(time.time())}.png")
            return "FAIL", None, _has_cf_clearance(sb), url_now, None, shot_path, False, "表单未出现"

        # 强制输入
        _force_input_value(sb, EMAIL_SEL, email)
        _force_input_value(sb, PASS_SEL, password)
        time.sleep(1)

        # ★ 截图1：输入后
        shot_after_input = screenshot(sb, f"STEP1_after_input_{safe_filename(email)}_{int(time.time())}.png")

        # 再次解决 CF 以免输入过程中出现新挑战
        _resolve_cf_challenge(sb)

        # ★ 截图2：点击前
        shot_before_click = screenshot(sb, f"STEP2_before_click_{safe_filename(email)}_{int(time.time())}.png")

        # 点击提交
        print("🔘 提交表单...")
        sb.uc_click(SUBMIT_SEL, reconnect_time=4)

        # ★ 截图3：点击后立即
        shot_after_click_immediate = screenshot(sb, f"STEP3_after_click_0s_{safe_filename(email)}_{int(time.time())}.png")

        # 等待页面反应
        time.sleep(4)
        # 提交后再尝试点击 captcha
        _try_click_captcha(sb, "提交后")

        # ★ 截图4：等待4秒后
        shot_after_4s = screenshot(sb, f"STEP4_after_4s_{safe_filename(email)}_{int(time.time())}.png")

        has_cf = _has_cf_clearance(sb)
        current_url = (sb.get_current_url() or "").strip()
        error_msg = _detect_login_error(sb)
        if error_msg:
            print(f"⚠️ 错误信息：{error_msg}")

        # 检测登录
        logged_in = False
        welcome_text = None
        for _ in range(10):
            logged_in, welcome_text = _is_logged_in(sb)
            if logged_in:
                break
            time.sleep(1)

        if not logged_in:
            # 最终失败截图
            final_shot = screenshot(sb, f"FAIL_final_{safe_filename(email)}_{int(time.time())}.png")
            reason = "未检测到登录成功标志"
            if error_msg:
                reason += f" | 错误: {error_msg}"
            return "FAIL", welcome_text, has_cf, current_url, None, final_shot, False, reason

        # 登录成功
        server_id, server_ok, server_reason = _post_login_visit(sb)

        try:
            current_url = sb.get_current_url().strip()
        except:
            pass

        final_shot = screenshot(sb, f"OK_{safe_filename(email)}_{server_id or 'no_server'}_{int(time.time())}.png")
        return "OK", welcome_text, has_cf, current_url, server_id, final_shot, server_ok, server_reason


def main():
    accounts = build_accounts_from_env()
    display = setup_xvfb()

    ok = 0
    fail = 0

    try:
        for i, acc in enumerate(accounts, start=1):
            email = acc["email"]
            password = acc["password"]
            tg_token = (acc.get("tg_token") or "").strip()
            tg_chat = (acc.get("tg_chat") or "").strip()

            safe_email = mask_email_keep_domain(email)

            print("\n" + "=" * 70)
            print(f"👤 [{i}/{len(accounts)}] 账号：{safe_email}")
            print("=" * 70)

            try:
                (
                    status,
                    welcome_text,
                    has_cf,
                    url_now,
                    server_id,
                    shot_path,
                    server_enter_ok,
                    server_enter_reason,
                ) = login_then_flow_one_account(email, password)

                if status == "OK":
                    ok += 1
                    msg = (
                        f"✅ Lunes BetaDash 登录成功\n"
                        f"账号：{safe_email}\n"
                        f"server_id：{server_id or '未提取到'}\n"
                        f"welcome：{welcome_text or '未读取到'}\n"
                        f"server页面：{'✅ 成功' if server_enter_ok else '❌ 失败'}\n"
                        f"说明：{server_enter_reason}\n"
                        f"当前页：{url_now}\n"
                        f"cf_clearance：{'OK' if has_cf else 'NONE'}"
                    )
                    print(msg)
                    if tg_token and tg_chat:
                        tg_send_photo(shot_path, msg, tg_token, tg_chat)

                else:
                    fail += 1
                    msg = (
                        f"❌ Lunes BetaDash 登录失败\n"
                        f"账号：{safe_email}\n"
                        f"welcome：{welcome_text or '未检测到'}\n"
                        f"当前页：{url_now}\n"
                        f"cf_clearance：{'OK' if has_cf else 'NONE'}\n"
                        f"说明：{server_enter_reason}"
                    )
                    print(msg)
                    if tg_token and tg_chat:
                        if shot_path and os.path.exists(shot_path):
                            tg_send_photo(shot_path, msg, tg_token, tg_chat)
                        else:
                            tg_send_text(msg, tg_token, tg_chat)

            except Exception as e:
                fail += 1
                msg = f"❌ 脚本异常\n账号：{safe_email}\n错误：{e}"
                print(msg)
                if tg_token and tg_chat:
                    tg_send_text(msg, tg_token, tg_chat)

            time.sleep(5)
            if i < len(accounts):
                time.sleep(5)

        print(f"\n📌 本次批量完成：登录成功 {ok} / 失败 {fail}")

    finally:
        if display:
            display.stop()


if __name__ == "__main__":
    main()
