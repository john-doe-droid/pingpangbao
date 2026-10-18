# 中国农业大学乒乓球社工作统计系统

社团内部使用的工作统计网站。**本阶段（P1）只建立工程底座**，尚未实现比赛、任务、提交与审核等业务功能。

- 后端：Django 5.2 LTS
- 数据库：PostgreSQL 18（开发与生产都是 PostgreSQL，**不使用 SQLite**）
- 前端：Django Templates + Bootstrap 5
- 生产：Gunicorn + Nginx + Docker Compose
- Python：3.13

> **给社团管理员的话**：本文档的每一步都可以直接复制粘贴执行。
> 遇到问题先看第 8 节「常见问题」。

---

## 目录

1. [准备环境（Windows）](#1-准备环境windows)
2. [配置本地环境变量](#2-配置本地环境变量)
3. [启动与停止服务](#3-启动与停止服务)
4. [运行测试](#4-运行测试)
5. [查看日志与排查常见错误](#5-查看日志与排查常见错误)
6. [数据库备份与恢复](#6-数据库备份与恢复)
7. [本地开发 vs 正式部署](#7-本地开发-vs-正式部署)
8. [常见问题](#8-常见问题)
9. [项目结构](#9-项目结构)

---

## 1. 准备环境（Windows）

### 1.1 需要装什么

| 软件 | 版本 | 说明 |
|------|------|------|
| Python | 3.13 | 后端语言 |
| PostgreSQL | 18 | 数据库 |
| Git | 任意较新版本 | 版本管理（可选，但推荐） |

> 如果以后要用 Docker 部署，还需要 Docker Desktop。**本阶段不要求**，见第 7 节说明。

### 1.2 安装 Python 3.13

打开 **PowerShell**（按 `Win` 键，输入 `powershell`，回车），执行：

```powershell
# 用 uv 安装并管理 Python（推荐，不需要管理员权限）
# 若还没装 uv，先装它：
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 安装 Python 3.13
uv python install 3.13

# 确认装好了（应显示 3.13.x）
uv python list --only-installed
```

> **看不到 `uv` 命令？** 关掉 PowerShell 重新打开一次，让 PATH 生效。

### 1.3 安装 PostgreSQL 18

从官网下载安装包：<https://www.postgresql.org/download/windows/>
（或直接下载免安装压缩包：<https://www.enterprisedb.com/download-postgresql-binaries>）

安装时请记下三件事：

1. **超级用户密码**（安装向导会让你设置）
2. **端口**（默认 `5432`）
3. **数据目录位置**

> ⚠️ **重要**：安装路径和数据库数据目录**不要包含中文**。
> PostgreSQL 在 Windows 上遇到中文路径可能报
> `invalid byte sequence for encoding "UTF8"`，导致初始化失败。
> 建议放在 `C:\pgsql` 或 `E:\pgsql` 这类纯英文路径下。

装完后验证（在 PowerShell 中）：

```powershell
# 把路径换成你的实际安装位置
& "C:\pgsql\bin\psql.exe" --version
# 预期输出类似：psql (PostgreSQL) 18.x
```

### 1.4 创建数据库和专用账号

```powershell
# 用超级用户连进去（会提示输入密码，就是安装时设的那个）
& "C:\pgsql\bin\psql.exe" -U postgres -h 127.0.0.1 -p 5432
```

连上后，在 `postgres=#` 提示符下逐条粘贴执行：

```sql
-- 1. 创建应用专用账号（把 'your-strong-password' 换成你自己的密码）
CREATE ROLE pingpong LOGIN PASSWORD 'your-strong-password';

-- 2. 允许该账号创建测试用数据库（跑测试需要）
ALTER ROLE pingpong CREATEDB;

-- 3. 创建项目数据库
CREATE DATABASE pingpong_dev OWNER pingpong ENCODING 'UTF8' TEMPLATE template0;

-- 4. 退出
\q
```

> **为什么不用 postgres 超级用户直接连？** 应用只应拥有自己需要的权限。
> 专用账号即使泄露，影响面也小得多。

### 1.5 进入项目目录

```powershell
cd "C:\Users\刘晨阳\Desktop\乒乓球社"
```

### 1.6 创建虚拟环境并安装依赖

```powershell
# 创建虚拟环境（只需做一次）
uv venv --python 3.13 .venv

# 安装项目依赖（只需做一次，或 requirements.txt 变化时重做）
uv pip install --python .\.venv\Scripts\python.exe -r requirements.txt
```

> **虚拟环境是什么？** 它把项目需要的 Python 包隔离在 `.venv` 文件夹里，
> 不会污染系统里其他 Python 程序。删掉 `.venv` 重装即可完全恢复。

---

## 2. 配置本地环境变量

### 2.1 复制样例文件

```powershell
Copy-Item .env.example .env
```

### 2.2 生成密钥

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(64))"
```

把输出的那一长串复制下来，下一步要用。

### 2.3 编辑 `.env`

用记事本打开：

```powershell
notepad .env
```

需要修改的地方：

```ini
# 把上一步生成的随机串粘到这里
DJANGO_SECRET_KEY=你生成的那一长串

# 数据库连接（密码要和第 1.4 节里设的一致）
POSTGRES_DB=pingpong_dev
POSTGRES_USER=pingpong
POSTGRES_PASSWORD=your-strong-password
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
```

保存并关闭。

> ⚠️ **`.env` 绝对不能提交到 Git**。它已经在 `.gitignore` 里了，不要手动改这个规则。
> `.env` 里存的是密码和密钥，一旦泄露，别人就能伪造登录身份。

### 2.4 初始化数据库表

```powershell
.\.venv\Scripts\python.exe manage.py migrate
```

看到一串 `OK` 就成功了。

### 2.5 创建管理员账号

```powershell
.\.venv\Scripts\python.exe manage.py createsuperuser
```

会提示输入：
- **学号**（作为登录名，例如 `admin001`）
- **姓名**
- **密码**（输入时不显示，正常现象；至少要 10 位）

> 创建出来的这个账号用于登录管理后台，给其他同学开账号。

---

## 3. 启动与停止服务

### 3.1 本地开发启动（日常用这个）

```powershell
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

看到下面这行就说明起来了：

```
Starting development server at http://127.0.0.1:8000/
```

然后在浏览器打开 <http://127.0.0.1:8000>

### 3.2 停止服务

在运行服务的那个窗口里按 **`Ctrl + C`**。

> ⚠️ **不要直接点窗口右上角的 ×** —— 那样可能留下后台进程占着 8000 端口，
> 下次启动会报「端口被占用」。

### 3.3 改密码 / 停用账号

浏览器打开 <http://127.0.0.1:8000/admin/>，用第 2.5 节创建的管理员账号登录。

- **新建账号**：用户 → 增加 → 填学号、姓名、初始密码
- **重置密码**：点开某个用户 → 密码字段旁的「修改密码」链接
- **停用账号**：取消勾选「账号启用」

> **首次登录会被强制改密码**。这是有意设计的：管理员设置的初始密码只用于第一次登录，
> 用户必须自己改成只有自己知道的密码。在改完之前，访问其它页面都会被跳回改密页。

### 3.4 无自助注册

本系统**没有**注册页面。所有账号由管理员在后台统一创建。

---

## 4. 运行测试

### 4.1 跑全部测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

预期输出类似：

```
52 passed in 15.88s
```

> 测试会**自动创建一个临时数据库**（名字以 `test_` 开头），跑完自动删除，
> 不会影响 `pingpong_dev` 里的真实数据。

### 4.2 只跑某一类测试

```powershell
# 只跑数据库相关（需要真实 PostgreSQL）
.\.venv\Scripts\python.exe -m pytest -m postgres -q

# 只跑生产配置安全检查
.\.venv\Scripts\python.exe -m pytest -m prod_config -q
```

### 4.3 一键完整验证（最重要）

```powershell
.\.venv\Scripts\python.exe scripts\verify_stage1.py
```

这个脚本会依次检查：Python 版本、依赖是否装全、Django 配置、生产配置安全性、
数据库连接、迁移状态、全部测试、健康检查、敏感信息泄露、容器配置等。

- **退出码 0** → 全部必要检查通过
- **退出码 1** → 有检查失败，看输出的「失败项」

结果同时写入 `verify_stage1_report.json`，可留档核对。

---

## 5. 查看日志与排查常见错误

### 5.1 应用日志

开发模式下日志**直接打印在运行服务的窗口里**，边操作边看即可。

生产模式（Docker 或 Linux）日志写到 `logs/app.log`，按 10MB 自动切分、保留 5 份：

```powershell
# 看最后 50 行
Get-Content logs\app.log -Tail 50

# 实时跟踪
Get-Content logs\app.log -Wait
```

### 5.2 数据库日志

PostgreSQL 的日志在数据目录下的 `log` 文件夹里，或看服务配置的 `log_directory`。

### 5.3 检查服务是否活着

```powershell
# 应用进程（不需要数据库）
curl.exe http://127.0.0.1:8000/health/live

# 应用 + 数据库都就绪
curl.exe http://127.0.0.1:8000/health/ready
```

- `/health/live` 返回 `{"status": "ok"}` → 应用正常
- `/health/ready` 返回 `{"status": "ok", "database": "ok"}` → 数据库也正常
- `/health/ready` 返回 **503** → 应用活着但连不上数据库，去查 `.env` 里的连接参数

---

## 6. 数据库备份与恢复

### 6.1 备份

```powershell
# 建个存放备份的文件夹
New-Item -ItemType Directory -Force -Path backups | Out-Null

# 备份（文件名带日期时间，不会互相覆盖）
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$env:PGPASSWORD="your-strong-password"
& "C:\pgsql\bin\pg_dump.exe" -h 127.0.0.1 -p 5432 -U pingpong -Fc -d pingpong_dev -f "backups\pingpong_dev-$stamp.dump"
$env:PGPASSWORD=""

# 确认文件生成了
Get-ChildItem backups
```

> `-Fc` 表示自定义压缩格式，恢复时更灵活。

### 6.2 恢复

```powershell
$env:PGPASSWORD="your-strong-password"

# 注意：恢复会覆盖同名对象。先确认你要恢复的是哪一份备份。
& "C:\pgsql\bin\pg_restore.exe" -h 127.0.0.1 -p 5432 -U pingpong `
    -d pingpong_dev --clean --if-exists "backups\pingpong_dev-20261018-120000.dump"

$env:PGPASSWORD=""
```

> ⚠️ **`--clean` 会先删除已有的表再重建**。生产环境执行前务必先做一次备份、
> 并确认当前没有人在使用系统。

### 6.3 建议

- **每周至少备份一次**，重要活动前额外备份一次
- 备份文件**不要只放在本机**：复制一份到网盘或另一块硬盘
- 定期演练恢复流程 —— 只有真正恢复成功过的备份才算有效备份

---

## 7. 本地开发 vs 正式部署

| | 本地开发 | 正式部署 |
|---|---|---|
| 配置文件 | `config.settings.dev` | `config.settings.prod` |
| DEBUG | 打开（方便看报错） | **强制关闭**（不接受环境变量覆盖） |
| 密钥 | 没配也能自动生成一个临时的 | **必须**自己提供，且长度 ≥ 40 |
| 数据库 | 本机 PostgreSQL | 服务器上的 PostgreSQL |
| HTTPS | 不需要 | 需要，相关安全项默认开启 |
| 启动方式 | `manage.py runserver` | Gunicorn + Nginx |
| 静态文件 | Django 直接服务 | Nginx 直接服务 |
| 报错信息 | 显示详细堆栈 | 只显示友好提示，细节进日志 |

### 7.1 切换方式

```powershell
# 本地开发（默认）
$env:DJANGO_SETTINGS_MODULE="config.settings.dev"

# 生产
$env:DJANGO_SETTINGS_MODULE="config.settings.prod"
```

### 7.2 正式部署概要

> ⚠️ **本阶段不创建云服务器、不购买域名。** 以下仅说明流程，供后续阶段使用。

1. 准备一台 Linux 服务器，装好 Docker 与 Docker Compose
2. 把代码传到服务器
3. 复制 `.env.example` 为 `.env`，填上**生产值**：
   ```ini
   DJANGO_SETTINGS_MODULE=config.settings.prod
   DJANGO_SECRET_KEY=<重新生成的随机串，不要用开发的那个>
   DJANGO_ALLOWED_HOSTS=你的域名.com,www.你的域名.com
   DJANGO_CSRF_TRUSTED_ORIGINS=https://你的域名.com
   POSTGRES_PASSWORD=<强密码>
   DJANGO_HTTPS_ENABLED=true
   ```
4. 把 `deploy/nginx/pingpong.conf` 里的 `stat.example.com` 替换成你的域名
5. 启动：
   ```bash
   docker compose up -d
   docker compose logs -f app     # 看日志确认起来了
   ```
6. 申请 HTTPS 证书（用 certbot），证书路径要和 Nginx 配置里一致

**重要安全约定（已在配置里落实）**：

- 数据库容器**不发布端口**，只在内部网络里，公网和宿主机都连不进去
- 应用容器也不直接对外，由 Nginx 反代
- 密钥、密码全部来自 `.env`，代码仓库里没有任何真实凭据

### 7.3 关于 Docker

Docker 配置（`Dockerfile`、`docker-compose.yml`、`deploy/nginx/pingpong.conf`）
**已经写好并通过结构校验**，但：

> **本机尚未安装 Docker，因此容器编排没有在本机实际跑过。**
> 这一点会在验证报告里明确标注为「未验证事项」，不会被算作已通过。

要在本机验证，需先安装 Docker Desktop（需要管理员权限），然后：

```powershell
docker compose config      # 先检查配置是否能解析
docker compose up -d       # 真正启动
docker compose ps          # 看健康状态
```

---

## 8. 常见问题

### 「python 不是内部或外部命令」

虚拟环境没激活，或者 Python 没装好。本项目所有命令都写成
`.\.venv\Scripts\python.exe ...` 的形式，**不依赖激活虚拟环境**，
请确认你在项目根目录下执行。

### 「No module named 'django'」

依赖没装。执行：

```powershell
uv pip install --python .\.venv\Scripts\python.exe -r requirements.txt
```

### 「connection refused」/「could not connect to server」

数据库没启动，或 `.env` 里的地址端口不对。检查：

```powershell
# 5432 端口是否在监听
netstat -an | Select-String ":5432"
```

没有输出说明 PostgreSQL 服务没起来，去「服务」里启动它。

### 「password authentication failed for user "pingpong"」

`.env` 里的 `POSTGRES_PASSWORD` 和第 1.4 节创建角色时设的密码不一致。
按第 1.4 节重建角色，或改 `.env` 里的密码。

### 「端口 8000 已被占用」

上次的服务没停干净。换一个端口：

```powershell
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8001
```

或者找出占用进程并结束它。

### 「invalid byte sequence for encoding "UTF8"」

PostgreSQL 遇到了**中文路径**。这是 Windows 版 PostgreSQL 的限制。
把数据库数据目录移到纯英文路径（例如 `E:\pgsql_data`）后重新初始化。

### 登录后一直跳回改密页

这是**正常行为** —— 新账号首次登录必须修改密码。改完就会进入首页。
如果改密时报「新密码过于简单」，请用至少 10 位、包含字母和数字的组合。

### 想清空数据库重新开始

```powershell
.\.venv\Scripts\python.exe manage.py flush --noinput
.\.venv\Scripts\python.exe manage.py createsuperuser
```

> ⚠️ `flush` 会**删除所有数据**。执行前先备份。

---

## 9. 项目结构

```
乒乓球社/
├── manage.py                     Django 命令行入口
├── requirements.txt              依赖清单（版本已锁定）
├── pytest.ini                    测试配置
├── .env.example                  环境变量样例（可提交）
├── .env                          你的真实配置（已忽略，不可提交）
├── .gitignore / .dockerignore
├── Dockerfile                    生产镜像（多阶段构建）
├── docker-compose.yml            应用 + 数据库 + Nginx 编排
│
├── config/                       项目配置
│   ├── settings/
│   │   ├── base.py               公共配置
│   │   ├── dev.py                本地开发配置
│   │   ├── prod.py               生产配置（含安全强制项）
│   │   └── build.py              镜像构建期专用（供 collectstatic）
│   ├── urls.py                   顶层路由
│   ├── wsgi.py / asgi.py         生产入口
│
├── accounts/                     用户与认证
│   ├── models.py                 User（学号登录）+ Role（多角色）
│   ├── admin.py                  管理员创建账号的界面
│   ├── backends.py               学号认证后端
│   ├── middleware.py             首登强制改密
│   ├── forms.py / views.py / urls.py
│   ├── migrations/0001_initial.py    ← 自定义用户模型的首批迁移
│   └── templates/accounts/       登录页、改密页
│
├── core/                         公共基础
│   ├── views.py                  首页 + 健康检查
│   ├── urls.py
│   ├── context_processors.py
│   ├── templates/core/home.html  首页模板
│   └── static/core/              Bootstrap 5 与站点样式
│
├── templates/base.html           全站基础模板
├── tests/                        自动化测试
│   ├── conftest.py
│   ├── test_accounts.py          用户模型与认证
│   ├── test_password_change.py   首登强制改密
│   ├── test_health.py            健康检查
│   ├── test_settings.py          生产配置安全约束
│   └── test_database.py          PostgreSQL 连接与迁移
│
├── scripts/
│   └── verify_stage1.py          本阶段统一验证入口
│
└── deploy/
    ├── gunicorn.conf.py          生产应用服务器配置
    └── nginx/pingpong.conf       Nginx 反向代理配置
```

### 关键文件速查

| 文件 | 作用 | 改它的时机 |
|---|---|---|
| `config/settings/base.py` | 公共配置、已安装应用清单 | 新增业务模块时 |
| `config/settings/dev.py` | 本地开发配置 | 一般不用改 |
| `config/settings/prod.py` | 生产安全配置 | 一般不用改 |
| `accounts/models.py` | 用户与角色模型 | 要加用户字段时 |
| `core/views.py` | 首页与健康检查 | 要加健康检查维度时 |
| `tests/conftest.py` | 测试公共 fixture | 新增测试需要共享数据时 |
| `scripts/verify_stage1.py` | 统一验证入口 | 新增检查项时 |
| `deploy/gunicorn.conf.py` | 生产应用服务器（Gunicorn）配置 | 调整并发/超时时 |
| `deploy/nginx/pingpong.conf` | Nginx 反向代理配置 | 换域名/证书时 |
| `docker-compose.yml` | 应用 + 数据库 + Nginx 编排 | 调整服务拓扑时 |
| `requirements.txt` | 依赖清单（已锁定版本） | 增删依赖时 |
| `.env.example` | 环境变量样例 | 新增环境变量时 |
| `pytest.ini` | 测试配置 | 调整测试发现规则时 |

### 后续业务模块的扩展位置

P2 阶段将新增以下应用，每个自带 `models.py / views.py / urls.py / tests`：

- `competitions` —— 比赛
- `tasks` —— 任务与人员分配
- `submissions` —— 工作提交
- `reviews` —— 审核
- `reports` —— 报表

新增后，在 `config/settings/base.py` 的 `LOCAL_APPS` 里登记，
并在 `config/urls.py` 里加一行 `path("<前缀>/", include("<app>.urls"))` 即可。

---

## 附：本阶段明确不做的事

- 比赛、任务分配、工作提交、审核、积分、Excel 报表（属于 P2 及以后）
- 公开自助注册（账号一律由管理员创建）
- 腾讯云 COS 等对象存储（附件功能阶段再实施）
- 创建云服务器、购买域名、支付云资源费用
