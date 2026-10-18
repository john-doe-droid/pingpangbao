# 中国农业大学乒乓球社工作统计系统

社团内部使用的工作统计网站。

- 后端：Django 5.2 LTS
- 数据库：PostgreSQL 18（开发与生产都是 PostgreSQL，**不使用 SQLite**）
- 前端：Django Templates + Bootstrap 5
- 生产：Gunicorn + Nginx + Docker Compose
- Python：3.13

## 当前进度

| 阶段 | 内容 | 状态 |
|------|------|------|
| P1 | 工程底座：账号、配置分层、健康检查、部署配置 | ✅ 已完成 |
| **P2** | **比赛、任务模板、人员分配、审核人授权、我的任务** | ✅ **已完成（本阶段）** |
| P3+ | 工作成果提交、附件上传、正式审核流程、Excel 报表、贡献积分 | ⬜ 未开始 |

> P2 只做「比赛与任务怎么安排」。**提交与审核还没做** —— 干事端页面会明确写出这一点，
> 不会给一个点了没反应的提交按钮。

> **给社团管理员的话**：本文档的每一步都可以直接复制粘贴执行。
> 日常操作请在浏览器里完成（见第 10 节），**不需要**登录 Django 后台或改数据库。
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
10. [日常操作指南（P2 比赛与任务）](#10-日常操作指南p2-比赛与任务)
11. [角色与权限说明](#11-角色与权限说明)

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

P1 底座：

```powershell
.\.venv\Scripts\python.exe scripts\verify_stage1.py
```

P2 比赛与任务模块（**本阶段用这个**）：

```powershell
.\.venv\Scripts\python.exe scripts\verify_stage2.py
```

`verify_stage2.py` 会依次检查：Python 与依赖、Django 配置、迁移状态、
PostgreSQL 连接、P2 模型与核心表、角色种子数据、路由挂载、
**P2 测试（140 项）**、**P1 回归测试（52 项，确保没有退化）**、
全套测试、权限不变量（独立验证）、写操作路由、P1 文件未被覆盖、
agent-loop 护栏未被修改、未提前实现后续阶段模块、README 一致性。

- **退出码 0** → 全部必要检查通过
- **退出码 1** → 有检查失败，看输出的「失败项」
- 标记为 ⏭️ 的是**环境阻塞**（例如本机没装 Docker），**不计入通过**，
  会单独列在「未验证事项」里

结果同时写入 `verify_stage2_report.json`，可留档核对。

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

P2 已新增：

- `competitions` —— 比赛、审核人授权
- `tasks` —— 工作类型、任务模板、比赛任务、人员分配

P3+ 将新增（每个自带 `models.py / views.py / urls.py / tests`）：

- `submissions` —— 工作成果提交与附件
- `reviews` —— 正式审核流程
- `reports` —— Excel 报表与积分

新增后，在 `config/settings/base.py` 的 `LOCAL_APPS` 里登记，
并在 `config/urls.py` 里加一行 `path("<前缀>/", include("<app>.urls"))` 即可。

---

## 10. 日常操作指南（P2 比赛与任务）

> 全部操作都在浏览器里完成，**不需要**登录 Django 后台、**不需要**改数据库。

### 10.1 页面入口一览

| 页面 | 网址 | 谁能打开 |
|------|------|----------|
| 我的任务（干事端） | `/my/tasks/` | 所有登录用户（只显示分配给自己的） |
| 比赛管理 | `/competitions/` | 所有登录用户；管理员可新建/修改/归档 |
| 比赛详情 | `/competitions/<编号>/` | 同上（草稿仅管理员可见） |
| 审核人授权 | `/competitions/<编号>/reviewers/` | 仅社团管理员 |
| 任务模板 | `/manage/tasks/templates/` | 仅社团管理员 |
| 工作类型 | `/manage/tasks/work-types/` | 仅社团管理员 |
| 在比赛下新建任务 | `/manage/tasks/competitions/<编号>/tasks/new/` | 仅社团管理员 |
| 任务管理（分配人员） | `/manage/tasks/tasks/<编号>/` | 仅社团管理员 |
| 建账号（后台） | `/admin/` | 仅超级管理员 |

本地开发时网址前缀是 `http://127.0.0.1:8000`。

### 10.2 完整流程：从零办一场比赛

**第一步：准备角色（只做一次）**

登录 `/admin/` → 角色，确认这三个角色存在（迁移时会自动建好）：

- `club_admin` 社团管理员
- `member` 干事
- `viewer` 全局查看

然后到「用户」里给同学分配角色：**要给谁派任务，就要勾上「干事」**。
一个用户可以同时勾多个角色。

**第二步：建工作类型（只做一次）**

`/manage/tasks/work-types/` → 新建工作类型。

系统初始是空的，建议先加上：宣传海报、推文撰写、推文排版、比赛摄影、视频剪辑、通知发布。
以后想加新类型直接在这里加，**不需要找人改代码**。

**第三步：建任务模板**

`/manage/tasks/templates/` → 新建模板。填：

- 模板名称（例如「赛后宣传推文」）
- 工作类型（从上面建的类型里选）
- 任务说明（交给干事看的交付要求）
- **材料要求**：每种材料勾「允许提交」和「要求必填」
  - 想让干事交文字说明 → 勾「文字说明」的允许 + 必填
  - 只想让他们交图片 → 勾「图片证明」的允许 + 必填，**不要**勾文字说明
  - ⚠️ 勾了「必填」必须同时勾「允许」，否则保存会报错
- 每名干事允许创建的记录数量（正整数，默认 1）

**第四步：建比赛**

`/competitions/` → 新建比赛。填名称、日期、默认截止时间。

新建出来是**草稿**状态：**干事看不到**，也不能配置任务。

**第五步：让比赛开始进行**

进比赛详情页 → 点「开始进行」。变成「进行中」后才能配任务。

**第六步：从模板添加任务**

比赛详情页 → 「＋ 从模板添加任务」→ 选模板、起名、可选填任务独立截止时间。

> 留空独立截止时间 = 继承比赛的默认截止；填了就优先用任务自己的。

**第七步：把任务分配给干事**

在比赛详情页点任务的「管理分配」→ 在下方多选框里选人（按住 `Ctrl` 多选）→ 确认分配。

被分配的干事马上就能在 `/my/tasks/` 看到这项任务。

**第八步：授权审核人（可选）**

比赛详情页 → 「管理审核人」→ 选人 → 添加授权。

> ⚠️ **审核权要一场一场单独授权**。就算你是管理员，没被授权也不能审核。
> 这是故意的：审核是有责任的动作。

**第九步：办完归档**

比赛详情页 → 「归档」→ 弹窗里**原样输入比赛名称**确认。

归档后：不能再加任务、不能再分配人员，但**既有记录全部保留**（不会删任何东西）。

### 10.3 干事怎么用

登录后点顶部导航的「我的任务」，或直接访问 `/my/tasks/`。

- 只显示**分配给你本人**的任务
- 每项显示：比赛名称、任务名称、工作类型、任务说明、截止时间、是否逾期
- 点「查看任务详情」看需要交什么材料

> **提交功能还没开放。** 详情页会写明「提交功能将在后续阶段开放」。
> 这是有意为之 —— 现在给个提交按钮也存不下来，不如不给。

### 10.4 改错了怎么办

| 情况 | 做法 |
|------|------|
| 模板要求写错了，还没建任务 | 直接编辑模板 |
| 模板要求写错了，**已经建了任务** | 编辑模板**不会影响已有任务**（它们冻结了当时的规则）。要给某场比赛换要求，就在那场比赛下用新模板另建一个任务 |
| 任务分配错了人 | 该任务页面点「取消分配」。记录会保留，不会丢失历史 |
| 审核人授权给错了 | 审核人页面点「撤销授权」 |
| 比赛信息填错了 | 详情页「修改信息」（**归档后不能改**） |
| 工作类型不想用了 | 点「停用」。它只会从新建任务的选项里消失，历史记录不受影响 |

---

## 11. 角色与权限说明

### 11.1 三个角色能做什么

| | 干事 `member` | 社团管理员 `club_admin` | 全局查看 `viewer` |
|---|---|---|---|
| 看自己的任务 | ✅ | ✅（若有分配） | ✅（若有分配） |
| 看比赛列表/详情 | ✅（非草稿） | ✅（含草稿） | ✅（非草稿） |
| 新建/修改/归档比赛 | ❌ | ✅ | ❌ |
| 维护工作类型、任务模板 | ❌ | ✅ | ❌ |
| 建任务、分配人员 | ❌ | ✅ | ❌ |
| 授权审核人 | ❌ | ✅ | ❌ |
| **审核工作成果** | 见下 | 见下 | 见下 |

### 11.2 审核权是独立的

**审核权不来自任何角色**，只能由管理员在每场比赛的「审核人」页面**逐场授权**。

这意味着：

- 管理员身份 ≠ 审核权
- 「全局查看」权限 ≠ 审核权
- 被授权审核某场比赛 ≠ 能管理这场比赛
- 被授权审核比赛 A ≠ 能审核比赛 B

### 11.3 权限是怎么保护的

- 干事看不到别人的任务：**后端查询层面**只取本人的分配记录，不是靠隐藏菜单
- 直接改网址没用：每个页面都会检查这条数据是否属于当前用户，不属于就返回 403
- 后台还有一道数据库约束：同一任务不能对同一人产生两条有效分配

---

## 附：本阶段明确不做的事

- 工作成果提交、附件上传、正式审核流程、Excel 报表、贡献积分（属于 P3 及以后）
- 公开自助注册（账号一律由管理员创建）
- 腾讯云 COS 等对象存储（附件功能阶段再实施）
- 创建云服务器、购买域名、支付云资源费用
- 比赛胜负、比分、球员出场、单双打或裁判判罚（**不属于本项目范围**）

---

## 附：本阶段已验证 / 未验证事项

> 这里如实区分「已经真实验证过」和「因环境限制没验证」。**后者不算通过。**

### 已实际验证（有可核对结果）

| 项目 | 验证方式 | 结果 |
|------|----------|------|
| Python 3.13 | 解释器实际版本 | 3.13.15 |
| Django 版本 | 运行时读取 | 5.2.18 |
| PostgreSQL 连接 | 实际连接并执行查询 | 18.1，`SELECT 1` 通过 |
| 迁移执行 | `manage.py migrate` 实际执行 | 全部 `OK`，含 `accounts.0001_initial` |
| 自定义用户表落库 | 直接查 `information_schema` | `accounts_user` 含 `student_id`、`must_change_password` 等列 |
| 自动化测试 | `pytest` | **52 项全部通过** |
| 健康检查 | 真实 HTTP 请求 | `/health/live`、`/health/ready` 均 200 |
| 首页与静态资源 | 真实 HTTP 请求 | 均 200；生产下静态资源带哈希名 |
| 生产 WSGI 入口 | 以 WSGI 协议调用 `config.wsgi:application` | 首页、登录页、健康检查、静态资源均 200 |
| 生产安全响应头 | 检查实际响应头 | `nosniff`、`DENY`、`same-origin` 到位 |
| 健康检查不泄露信息 | 比对响应体与真实密钥 | 未出现密钥或数据库口令 |
| `.env` 未进仓库 | `git ls-files` + `check-ignore` | 确认未被跟踪 |
| 验证入口退出码 | 正常跑 + 人为制造失败 | 正常 `0`；失败 `1` |
| 移动端布局 | 390×844 真实浏览器截图 | 无横向溢出，表单满宽，导航同行 |
| 桌面布局 | 1440×900 真实浏览器截图 | Bootstrap 组件与中文渲染正常 |
| agent-loop 护栏 | 比对文件哈希 | 与任务开始时一致，未被修改 |

### 未验证（环境限制，**不代表通过**）

| 项目 | 原因 | 已做的替代验证 |
|------|------|----------------|
| Docker 容器编排实际启动 | 本机**未安装 Docker**，且无管理员权限、winget 不可用 | 对 `docker-compose.yml` 做了结构校验：PG18 镜像、数据库不发布端口、网络 `internal: true`、healthcheck、重启策略均已确认 |
| Gunicorn 实际运行 | Gunicorn 依赖 Unix 专有的 `fcntl`，**无法在 Windows 运行** | 用 WSGI 协议直接调用同一个 `config.wsgi:application`，验证了入口代码本身可用 |
| Nginx 反向代理实际生效 | 同上，本机无 Linux/容器环境 | 配置文件已交付，路径与上游服务名与 compose 一致 |
| HTTPS 证书链路 | 本阶段不购买域名、不创建服务器 | 生产配置中的 HTTPS 安全项（Secure Cookie、HSTS、SSL 重定向）已由测试覆盖 |

**要完成这三项验证，需要在 Linux 环境或装有 Docker Desktop 的机器上执行：**

```bash
docker compose config     # 检查配置可解析
docker compose up -d      # 启动全套
docker compose ps         # 确认各服务 healthy
curl -I http://localhost/health/ready
```

---

## 附：下一阶段（P2）开始前需要确定的事项

以下是实现比赛、任务、人员分配模块**之前必须先定下来**的业务规则。
不提前替社团做决定，先列出来供讨论：

1. **角色划分**
   目前只建了角色表结构，具体要有哪些角色（队员 / 教练 / 裁判 / 物资管理员 / 社长？）
   以及每个角色能做什么，需要社团确认。

2. **比赛的数据模型**
   一次比赛是否需要区分"赛事 → 场次 → 单局"多层？双打如何表示？
   是否需要记录对手、比分、胜负？比赛与队员的关联是一对多还是多对多？

3. **任务分配规则**
   任务是"派给某人"还是"某人认领"？一个任务能否多人协作？
   是否有截止时间与逾期状态？任务能否被驳回或转派？

4. **提交与审核的流转**
   提交后由谁审核？审核不通过是退回重做还是直接作废？
   是否需要保留每一次提交的历史版本？附件如何存储（这是 COS 接入的时机）？

5. **统计口径**
   "工作统计"具体统计什么维度 —— 出勤次数？比赛场次？胜负率？任务完成数？
   按月/按学期/按人 还是按比赛？这直接决定数据模型和报表设计。

6. **积分规则（如果要做）**
   积分怎么算、谁有权调整、是否允许手工修正、历史积分是否可追溯。

7. **Excel 报表范围**
   导出哪些表、包含哪些列、是否需要固定模板（社团以前用过什么表格？）。

8. **账号开通流程**
   谁负责在后台建号？批量导入是否需要（例如按会员名单 Excel 一次性建号）？
   离职/毕业成员的账号是停用还是删除？

9. **数据迁移**
   社团现有的历史数据（以前的 Excel/纸质记录）是否需要导入系统？

10. **权限边界**
    普通队员能否看到他人的工作统计？敏感信息（手机号）对谁可见？
