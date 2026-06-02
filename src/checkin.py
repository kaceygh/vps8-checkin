"""Lunes Host (betadash.lunes.host) 登录即签到主流程。

环境变量：
    VPS8_EMAIL    (必填) 登录邮箱
    VPS8_PASSWORD (必填) 登录密码
    TELEGRAM_BOT_TOKEN (可选)
    TELEGRAM_CHAT_ID   (可选)
    GITHUB_RUN_URL     (可选，由 workflow 注入)

退出码：
    0 - 登录(签到)成功
    1 - 重试 3 次后仍失败
    2 - 配置错误（邮箱/密码缺失）
"""

from __future annotations

import os
import sys
import time
import traceback

from . import browser, notifier
from .env import load_local_env

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


def _visible_page_text(page) -> str:
    try:
        text = page.run_js("return document.body ? document.body.innerText : '';")
        return (text or "").replace(" ", " ")
    except Exception as exc:
        print(f"[checkin] 获取页面可见文本失败: {exc}")
        return ""


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
    time.sleep(3)  # 给页面初始加载留出时间

    # =================【核心修复 1：前置破盾逻辑】=================
    print("[checkin] 开始扫描页面，检查是否触发 Cloudflare 真人验证盾...")
    
    for cf_attempt in range(1, 6):
        html_content = page.html
        page_text = _visible_page_text(page)
        
        # 匹配是否卡在验证页
        if "challenges.cloudflare.com" in html_content or "Verify you are human" in page_text or "challenge" in page.url:
            print(f"[checkin] [警告] 确认遭遇 Cloudflare 拦截 (尝试穿透 {cf_attempt}/5)...")
            browser.screenshot(page, f"00-cf-intercept-{cf_attempt}")
            
            # 寻找拦截盾的 Iframe 容器
            iframe = page.ele("tag:iframe[src*='challenges.cloudflare.com']", timeout=5)
            if iframe:
                print("[checkin] 成功定位到 Cloudflare 验证框 Iframe")
                try:
                    # 强力穿透：直接模拟点击 Iframe 中心坐标点，通常可以直接盲点命中复选框
                    print("[checkin] 尝试执行 Iframe 中心位物理盲点击...")
                    iframe.click()
                except Exception as e:
                    print(f"[checkin] 盲点 Iframe 失败: {e}")
                
                # 同时调用项目原生的 solve_turnstile 逻辑双保险兜底
                try:
                    browser.solve_turnstile(page, timeout=10)
                except Exception:
                    pass
            else:
                print("[checkin] 未找到明确的 Iframe 标签，直接调用原生破盾函数兜底...")
                try:
                    browser.solve_turnstile(page, timeout=15)
                except Exception:
                    pass
            
            time.sleep(5)  # 每次点击后稳一稳，让浏览器执行刷新或跳转
        else:
            if cf_attempt > 1:
                print("[checkin] Cloudflare 验证盾已消失，准备检索表单。")
            break

    # =================【核心修复 2：异步轮询检索表单】=================
    print("[checkin] 正在检索登录表单...")
    pass_input = None
    for i in range(15):  # 总计最大等待 30 秒
        pass_input = (
            page.ele("@@tag()=input@@type=password", timeout=1) or
            page.ele("@@tag()=input@@name=password", timeout=1) or
            page.ele("@@tag()=input@@placeholder:密码", timeout=1) or
            page.ele("@@tag()=input@@id=password", timeout=1)
        )
        if pass_input:
            print(f"[checkin] 成功进入登录表单页面 (耗时 {i*2} 秒)")
            break
            
        # 容错：如果在等表单期间验证盾又刷新出来了，顺手再点一下
        if "challenges.cloudflare.com" in page.html:
            print("[checkin] 检索表单期间盾牌复活，尝试进行追加点击...")
            try:
                ifr = page.ele("tag:iframe[src*='challenges.cloudflare.com']", timeout=2)
                if ifr:
                    ifr.click()
            except Exception:
                pass
                
        time.sleep(2)

    if not pass_input:
        browser.screenshot(page, "01-login-no-form")
        raise CheckinElementsNotFound("未能突破 Cloudflare 拦截页，登录表单未渲染。请查看最新推送的错误截图")

    browser.screenshot(page, "01-login-page")

    # 3. 填写表单
    _fill_email_and_password(page, email, password)

    # 4. 再次检查登录表单内部是否有内嵌式 Turnstile 验证
    print("[checkin] 检查表单内部是否含有内嵌式 Turnstile...")
    try:
        browser.solve_turnstile(page, timeout=15)
    except Exception:
        pass
    time.sleep(1)

    browser.screenshot(page, "01a-after-turnstile")

    # 5. 点击登录并等待跳转
    _click_login_button(page)

    if not _wait_until_logged_in(page, timeout=30):
        browser.screenshot(page, "01b-login-stuck")
        raise LoginFailed(f"登录后仍停留在 {page.url}")

    browser.screenshot(page, "02-after-login")


def do_checkin(page, email: str, password: str) -> str:
    """执行登录即签到流程。"""
    _login(page, email, password)

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
