# WeRead MP Web

独立的自用网站：读取你在微信读书中已关注的公众号文章，筛选后批量导出为带 YAML Front Matter 的 Markdown ZIP。项目不修改浏览器扩展，也不保存账号、Cookie 或文章数据库。

## 本地运行

需要 Python 3.10 或更新版本。先安装依赖并设置访问密码：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export APP_PASSWORD='请替换为至少 12 位的随机密码'
export COOKIE_SECURE=false
uvicorn app.main:app --host 127.0.0.1 --port 8765 --workers 1
```

打开 <http://127.0.0.1:8765/>。`COOKIE_SECURE=false` 只用于本机 HTTP；通过 HTTPS 访问时保持 `true`。

## Docker Compose

```bash
cp .env.example .env
# 编辑 .env，设置 APP_PASSWORD
docker compose up -d --build
docker compose logs -f weread-mp-web
```

默认只发布到 `127.0.0.1:8765`，不会直接暴露到公网。停止服务：

```bash
docker compose down
```

容器只运行一个 Uvicorn worker，因为登录会话、微信读书 Cookie、文章列表、任务状态和临时 ZIP 都保存在进程内存中。重启或停止容器会清除这些数据，Compose 不挂载持久数据卷。

## Cookie 连接

在你自己的浏览器中登录 <https://weread.qq.com/web/shelf>，开发者工具的 Network 请求中复制 Cookie 请求头，粘贴到网站的连接表单。服务会先调用书架接口验证，成功后只在当前服务进程内存中保留；断开、会话过期或进程重启都会清除。Cookie 不写入日志、浏览器 localStorage、ZIP 或文件。

微信读书的历史列表是未公开接口，页面只展示上游实际返回的文章和日期范围。遇到登录过期、验证码、限流或网络错误时，请在微信读书网页完成正常验证后重新连接；项目不会绕过验证码或访问控制。

## 归档流程

连接成功后选择公众号，读取列表，再使用标题/摘要搜索、日期范围筛选和勾选。开启“下载图片”后，服务只请求微信文章和微信媒体域名，图片失败会保留原始远程链接并在任务结果中显示失败数量。每篇文章生成 Markdown，所有结果打成临时 ZIP；下载完成后服务删除 ZIP。

## 反向代理

如果要从其他设备访问，请在已有网关后配置 HTTPS，并将 `/` 和 `/api/` 转发到 `127.0.0.1:8765`。不要把 Uvicorn 端口直接暴露到互联网；生产环境保持 `COOKIE_SECURE=true`。建议在代理层再增加访问控制和请求体限制。

## 开发检查

```bash
PYTHONPATH=/tmp/weread-mp-web-packages python3 -m pytest -q
docker build -t weread-mp-web .
```

自动化测试使用合成 Cookie 和假的上游响应，不读取真实微信读书会话。
