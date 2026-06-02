"""Lunes Host (betadash.lunes.host) 登录即签到主流程。

环境变量：
    VPS8_EMAIL    (必填) 登录邮箱
    VPS8_PASSWORD (必填) 登录密码
    TELEGRAM_BOT_TOKEN (可选)
    TELEGRAM_CHAT_ID   (可选)
    GITHUB_RUN_URL     (可选，由 workflow 注入)
    VPS8_USER_AGENT    (可选) 自定义 UA

退出码：
    0 - 登录(签到)成功
    1 - 重试 3 次后仍失败
    2 - 配置错误（邮箱/密码缺失）
"""

from __future__ import annotations

import os
import sys
import time
import traceback

from . import browser, notifier
from .env import load_local_env

# 目标地址改为 betadash.lunes.host
BASE_URL = "https://betadash.lunes.host"
LOGIN_URL = f"{BASE_URL}/login"

MAX_ATTEMPTS = 3
RETRY_INTERVAL_SECONDS = 30
SUCCESS_SNAPSHOT_DELAY_SECONDS = 5


class LoginFailed(Exception):
    """登录后未能跳出登录页。"""


class CheckinElementsNotFound(Exception):
    """页面上找不到关键元素（按钮/输入框等）。"""


def _get_env_or_die(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        print(f"[fatal] 环境变量 {name} 未设置")
        sys.exit(2)
    return val


def _fill_email_and_password(page, email: str, password: str) -> None:
    """根据登录页结构填入邮箱和密码。"""
    email_input = (
        page.ele("@@tag()=input@@type=email", timeout=2)
        or page.ele("@@tag()=input@@name=email", timeout=1)
        or page.ele("@@tag()=input@@placeholder:邮箱", timeout=1)
        or page.ele("@@tag()=input@@id=email", timeout=1)
    )
    if not email_input:
        browser.screenshot(page, "01-login-no-email-input")
        raise CheckinElementsNotFound("找不到邮箱输入框")

    pass_input = (
        page.ele("@@tag()=input@@type=password", timeout=2)
        or page.ele("@@tag()=input@@name=password", timeout=1)
        or page.ele("@@tag()=input@@placeholder:密码", timeout=1)
        or page.ele("@@tag()=input@@id=password", timeout=1)
    )
    if not pass_input:
        browser.screenshot(page, "01-login-no-pass-input")
        raise CheckinElementsNotFound("找不到密码输入框")

    email_input.click()
    email_input.clear()
    email_input.input(email)
    time.sleep(0.3)

    pass_input.click()
    pass_input.clear()
    pass_input.input(password)
    time.sleep(0.3)


def _click_login_button(page) -> None:
    """点击登录按钮，避开第三方登录按钮。"""
    js = r"""
    const isVisible = (el) => {
      const style = window.getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.display !== 'none'
        && style.visibility !== 'hidden'
        && rect.width > 0
        && rect.height > 0;
    };
    const blacklist = ['github', 'google', 'nodeloc', 'telegram', '注册', '忘记'];
    const candidates = Array.from(document.querySelectorAll('button, [role="button"], input[type="submit"], a.btn'));
    const target = candidates.find((el) => {
      if (!isVisible(el)) return false;
      const text = (el.innerText || el.textContent || el.value || '').trim();
      if (!text) return false;
      const lower = text.toLowerCase();
      if (blacklist.some((b) => lower.includes(b))) return false;
      return text === '登录' || text === '登 录' || text.includes('登录') || lower === 'login' || lower === 'sign in';
    });
    if (!target) return false;
    target.scrollIntoView({block: 'center', inline: 'center'});
    target.click();
    return true;
    """
    try:
        clicked = bool(page.run_js(js))
    except Exception as exc:
        print(f"[checkin] JS 点击登录按钮失败: {exc}")
        clicked = False

    if not clicked:
        submit_btn = page.ele("tag:button@type=submit", timeout=2)
        if submit_btn:
            submit_btn.click()
            return
        browser.screenshot(page, "01-login-no-button")
        raise CheckinElementsNotFound("找不到登录按钮")

    print("[checkin] 已点击登录")


def _wait_until_logged_in(page, timeout: int = 30) -> bool:
    """等待登录后页面跳出 /login。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if "/login" not in page.url:
            print(f"[checkin] 登录后跳转到: {page.url}")
            time.sleep(1)
            return True
        time.sleep(0.5)
    return False


def _login(page, email: str, password: str) -> None:
    print(f"[checkin] 访问登录页: {LOGIN_URL}")
    page.get(LOGIN_URL)

    # 【核心修复1】第一时间处理前置的 Cloudflare 盾 (应对截图中的拦截)
    print("[checkin] 正在等待页面初始加载，检查是否遭遇 Cloudflare 前置盾...")
    time.sleep(4) 
    try:
        browser.solve_turnstile(page, timeout=20)
    except Exception:
        pass  # 如果没有盾，或者处理模块报错，继续往下走

    # 【核心修复2】采用轮询方式等待密码框，给予 CF 验证后跳转充足的时间
    print("[checkin] 等待登录表单渲染...")
    pass_input = None
    for _ in range(12): # 每次等2秒，总计等 24 秒
        pass_input = (
            page.ele("@@tag()=input@@type=password", timeout=1) or
            page.ele("@@tag()=input@@name=password", timeout=1) or
            page.ele("@@tag()=input@@placeholder:密码", timeout=1) or
            page.ele("@@tag()=input@@id=password", timeout=1)
        )
        if pass_input:
            break
        time.sleep(2)

    if not pass_input:
        browser.screenshot(page, "01-login-no-form")
        raise CheckinElementsNotFound("登录表单未渲染，可能死卡在 Cloudflare 验证页，请查看截图")

    browser.screenshot(page, "01-login-page")

    _fill_email_and_password(page, email, password)

    # 处理表单内部可能含有的 Turnstile 验证码
    print("[checkin] 尝试处理表单上的 Cloudflare Turnstile")
    turnstile_ok = browser.solve_turnstile(page, timeout=30)
    if not turnstile_ok:
        print("[checkin] 未发现或未通过 Turnstile，继续尝试登录")
    else:
        time.sleep(1)

    browser.screenshot(page, "01a-after-turnstile")

    _click_login_button(page)

    if not _wait_until_logged_in(page, timeout=30):
        browser.screenshot(page, "01b-login-stuck")
        raise LoginFailed(f"登录后仍停留在 {page.url}")

    browser.screenshot(page, "02-after-login")


def do_checkin(page, email: str, password: str) -> str:
    """执行登录即签到流程。"""
    _login(page, email, password)

    # 登录成功后，等待几秒钟让用户面板完全加载，以便截图更完整
    print("[checkin] 登录成功，正在等待控制面板加载...")
    time.sleep(SUCCESS_SNAPSHOT_DELAY_SECONDS)
    
    browser.screenshot(page, "05-success")
    print("[checkin] 登录完成，即视为签到成功！")
    return "登录即签到成功"


def _send_result_snapshot(page, status: str, filename: str) -> None:
    result_screenshot = browser.screenshot(page, filename)
    if result_screenshot:
        notifier.send_result_photo(status, result_screenshot)


def main() -> int:
    loaded_env = load_local_env()
    if loaded_env:
        print(f"[env] 已从本地 env 文件加载: {', '.join(loaded_env)}")

    email = _get_env_or_die("VPS8_EMAIL")
    password = _get_env_or_die("VPS8_PASSWORD")
    browser.clean_screenshots()

    last_error: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"\n========== 尝试 {attempt}/{MAX_ATTEMPTS} ==========")
        page = None
        try:
            page = browser.create_page()
            status = do_checkin(page, email, password)
            _send_result_snapshot(page, status, "06-result")
            print("[main] 任务完成")
            return 0
        except Exception as exc:
            last_error = exc
            print(f"[main] 第 {attempt} 次失败: {type(exc).__name__}: {exc}")
            traceback.print_exc()
            if page is not None:
                browser.screenshot(page, f"failure-attempt-{attempt}")
        finally:
            browser.safe_close(page)

        if attempt < MAX_ATTEMPTS:
            print(f"[main] {RETRY_INTERVAL_SECONDS}s 后重试...")
            time.sleep(RETRY_INTERVAL_SECONDS)

    summary = f"{type(last_error).__name__}: {last_error}" if last_error else "未知错误"
    print(f"\n[main] {MAX_ATTEMPTS} 次尝试均失败: {summary}")
    notifier.send_failure(summary, MAX_ATTEMPTS)
    failure_screenshot = browser.SCREENSHOT_DIR / f"failure-attempt-{MAX_ATTEMPTS}.png"
    if failure_screenshot.exists():
        notifier.send_result_photo(f"登录失败: {summary}", failure_screenshot)
    return 1


if __name__ == "__main__":
    sys.exit(main())
