# WeRead MP Web

独立的自用网站：读取你在微信读书中已关注的公众号文章，筛选后批量导出为带 YAML Front Matter 的 Markdown ZIP。项目不修改浏览器扩展，也不保存账号、Cookie 或文章数据库。

完整的首次使用和排障步骤见 [操作指引](docs/operation-guide.md)。

## 本地运行

需要 Python 3.10 或更新版本。先安装依赖并启动服务：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export COOKIE_SECURE=false
uvicorn app.main:app --host 127.0.0.1 --port 8765 --workers 1
```

打开 <http://127.0.0.1:8765/>。`COOKIE_SECURE=false` 只用于本机 HTTP；通过 HTTPS 访问时保持 `true`。

## Docker Compose

```bash
cp .env.example .env
# 编辑 .env，确认 COOKIE_SECURE 等部署配置
docker compose up -d --build
docker compose logs -f weread-mp-web
```

默认只发布到 `127.0.0.1:8765`，不会直接暴露到公网。停止服务：

```bash
docker compose down
```

容器只运行一个 Uvicorn worker，因为匿名浏览器会话、微信读书 Cookie、文章列表、任务状态和临时 ZIP 都保存在进程内存中。重启或停止容器会清除这些数据，Compose 不挂载持久数据卷。

网站不再设置访问密码，打开页面后会直接进入微信读书扫码流程。服务只用一个 HttpOnly 会话 Cookie 关联当前浏览器的扫码任务和文章数据；部署到公网前必须在反向代理、VPN 或防火墙层增加访问控制，不能把本服务端口直接暴露到互联网。

## Vercel 部署

仓库包含 `api/index.py` 和 `vercel.json`，可以把现有 FastAPI 应用作为 Vercel Python Function 部署。先在本机安装 Node.js 18 或更新版本，然后登录并创建预览部署：

```bash
npx vercel@latest login
npx vercel@latest link
npx vercel@latest deploy --yes
```

确认预览地址可以打开首页和 `/healthz` 后，再发布生产版本：

```bash
npx vercel@latest deploy --prod --yes
```

在 Vercel 项目环境变量中按部署环境设置 `COOKIE_SECURE=true`，可选调整 `SESSION_TTL_SECONDS`、`DEFAULT_LIST_ARTICLES`、`MAX_ARCHIVE_ARTICLES` 和 `ARTIFACT_TTL_SECONDS`。不要把微信读书 Cookie 写入环境变量；它只通过扫码流程进入当前内存会话。

Vercel Function 的实例和临时文件不保证长期驻留。扫码任务、微信读书 Cookie、文章列表、后台归档任务和临时 ZIP 都是进程内存数据，实例切换、重新部署或函数回收都会让会话失效；较大的批量导出也可能受函数执行时限影响。Vercel 部署适合个人低频使用和预览，要求稳定运行时请使用单 worker 的 Docker Compose，并在反向代理、VPN 或防火墙层增加访问控制。

## 连接方式与页面

页面采用“连接微信读书 → 选择文章 → 下载文件”三步布局，包含公众号搜索、收藏筛选、标题搜索、日期检索、吸底选择栏和下载进度弹窗。

扫码连接已接入微信读书当前网页协议：打开网站后会自动向微信读书申请一次性 UID，生成真实 `/web/confirm` 二维码，并在服务端长轮询登录确认。二维码默认有效约 3 分钟，过期后可重新生成；扫码成功后服务端会直接验证书架并进入公众号列表。部分账号会在手机确认后要求双重验证码：页面会显示 4 位验证码输入框，输入手机显示的数字后继续完成登录。验证码只在当前内存任务中短暂使用，不记录、不保存、不返回给浏览器；浏览器也不会收到 `wr_skey`、访问令牌或完整 Cookie。手动连接只在用户展开“高级连接 / 异常诊断”后显示，作为微信读书协议异常或上游临时不可用时的诊断旁路。

在你自己的浏览器中登录 <https://weread.qq.com/web/shelf>，开发者工具的 Network 请求中复制 Cookie 请求头，粘贴到网站的连接表单。服务会先调用书架接口验证，成功后只在当前服务进程内存中保留；断开、会话过期或进程重启都会清除。Cookie 不写入日志、浏览器 localStorage、ZIP 或文件。

微信读书的历史列表是未公开接口。首次打开公众号时默认读取最近 1 年内的文章；需要调整范围时填写开始或结束日期并点击“按日期读取”，服务会继续使用上游分页游标读取，直到命中日期边界、上游终止或达到单次安全上限。页面只展示上游实际返回的文章和日期范围，不保证微信读书接口之外的历史内容。遇到二维码过期、验证码错误、限流或网络错误时，请按页面提示重新扫码或输入手机显示的验证码；项目不会绕过验证码或访问控制。

## 归档流程

连接成功后选择公众号，读取列表，再使用标题搜索、日期检索和勾选。公众号星标收藏只保存在当前浏览器的 localStorage 中，可在“全部 / 已收藏”之间切换，不会上传到服务端。开启“下载图片”后，服务只请求微信文章和微信媒体域名，图片失败会保留原始远程链接并在任务结果中显示受影响的文章数。任务展示实际逐篇状态，失败项可单独重试；重试前的成功文件仍可在弹窗中分别下载。下载图片默认关闭。每篇文章生成 Markdown，所有结果打成临时 ZIP；下载完成后服务删除 ZIP。

## 反向代理

如果要从其他设备访问，请在已有网关后配置 HTTPS，并将 `/` 和 `/api/` 转发到 `127.0.0.1:8765`。不要把 Uvicorn 端口直接暴露到互联网；生产环境保持 `COOKIE_SECURE=true`。建议在代理层再增加访问控制和请求体限制。

## 开发检查

Python 测试会运行登录页面的 JavaScript 行为回归，开发环境需要 Node.js 18 或更新版本；生产容器不需要 Node.js。

```bash
pip install -r requirements-dev.txt
python3 -m pytest -q
docker build -t weread-mp-web .
npx vercel@latest build
```

自动化测试使用合成 Cookie 和假的上游响应，不读取真实微信读书会话。

## 设计与验收

页面参考保留在 `docs/wereaddesign/`，实现与限制记录在 `design-qa.md`。本地合成数据验收服务位于 `tests/ui_server.py`，只用于开发，不包含在 Docker 镜像中；生产启动始终使用 `app.main:app`。
