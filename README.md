# VPS8 自动签到

通过 GitHub Actions 定时访问 [vps8.zz.cd](https://vps8.zz.cd/login) 完成签到，自动处理 Cloudflare Turnstile 盾。

## 工作原理

1. GitHub Actions 触发（定时或手动 `workflow_dispatch`）
2. ubuntu runner 安装 Python + Xvfb
3. `xvfb-run` 启动虚拟显示器，DrissionPage 启动有头 Chrome
4. 脚本访问登录页 → 填入邮箱/密码 → 处理 Cloudflare Turnstile → 点击「登录」
5. 登录成功后点击顶部导航栏「签到」→ 在签到页点击签到按钮 → 确认成功
6. 签到成功后按北京时间日期写入 Actions cache，当天后续运行会直接跳过
7. 运行结束后推送结果截图到 Telegram；失败重试最多 3 次

## 目录结构

```
.
├── .github/workflows/checkin.yml   # GitHub Actions 工作流
├── src/
│   ├── checkin.py                  # 主流程
│   ├── browser.py                  # DrissionPage + Turnstile
│   ├── notifier.py                 # Telegram 通知
│   └── env.py                      # 本地 .env 加载
├── requirements.txt
└── README.md
```

## 配置

### 1. 在仓库 Settings → Secrets and variables → Actions 添加

| 名称 | 必填 | 用途 |
|---|:---:|---|
| `VPS8_EMAIL` | 是 | 站点登录邮箱 |
| `VPS8_PASSWORD` | 是 | 站点登录密码 |
| `TELEGRAM_BOT_TOKEN` | 否 | Telegram Bot Token |
| `TELEGRAM_CHAT_ID` | 否 | 接收结果截图和失败通知的 chat id |
| `VPS8_USER_AGENT` | 否 | 调试时固定浏览器 UA；默认按运行环境自动生成 |

不配置 Telegram 也能运行，仅不会推送结果截图和失败通知。

### 2. 调整运行频率

编辑 `.github/workflows/checkin.yml` 里的 `cron`：

- 每天一次（北京时间 9:23）：`'23 1 * * *'`
- 每 8 小时（默认）：`'23 1,9,17 * * *'`

提示：避开整点（`0` 或 `30` 分），分散负载。

## 本地调试

```bash
# 1. 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 2. 装依赖
pip install -r requirements.txt

# 3. 设置环境变量
cp .env.example .env.local
# 编辑 .env.local 填入你的邮箱和密码

# 4. 运行（macOS / Linux 桌面环境会弹真实浏览器，便于排错）
python -m src.checkin
```

每次运行开始会清理 `screenshots/*.png`，关键节点截图：

| 文件 | 时机 |
|---|---|
| `01-login-page.png` | 打开登录页 |
| `01a-after-turnstile.png` | Turnstile 处理完成 |
| `01b-login-stuck.png` | 登录失败时（停在 /login） |
| `02-after-login.png` | 登录成功 |
| `03-checkin-page.png` | 进入签到页 |
| `04-after-click-checkin.png` | 点击签到按钮之后 |
| `05-success.png` | 确认签到成功 |
| `06-result.png` | 最终发送到 Telegram 的结果截图 |
| `failure-attempt-{n}.png` | 第 n 次重试失败时 |

## 故障排查

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 找不到登录按钮 / 输入框 | 页面 DOM 改了 | 看 `01-login-page.png`，调整 `src/checkin.py` 里 `_fill_email_and_password` / `_click_login_button` 的选择器 |
| Turnstile 卡死 | runner IP 被风控 / DrissionPage 版本旧 | 重试几次；升级 `pip install -U DrissionPage` |
| 登录成功但未跳转签到页 | 导航「签到」文案变了 | 看 `02-after-login.png`，调整 `_go_to_checkin_page` 的关键字 |
| 签到按钮找不到 | 签到页按钮文案变了 | 看 `03-checkin-page.png`，调整 `_click_checkin_action` 的关键字 |

## 风险声明

仅供学习与个人自动化使用。请遵守目标站点的服务条款，自负使用风险。
