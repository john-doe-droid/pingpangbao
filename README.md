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
| P2 | 比赛、任务模板、人员分配、审核人授权、我的任务 | ✅ 已完成 |
| P3 | 工作成果提交、图片处理、私有附件存储、审核闭环 | ✅ 已完成 |
| **P4** | **统计与 Excel 报表（按人员 / 按工作类型 / 明细）** | ✅ **已完成（本阶段）** |
| P5 | 部署到云服务器（服务器本地磁盘保存附件） | ⬜ 未开始 |

> P4 只做**统计与报表**，**不包含贡献积分**。积分规则尚未确定，未实现。

> **给社团管理员的话**：本文档的每一步都可以直接复制粘贴执行。
> 日常操作请在浏览器里完成（见第 10 / 12 / 14 节），**不需要**登录 Django 后台或改数据库。
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
12. [提交与审核操作指南（P3）](#12-提交与审核操作指南p3)
13. [图片处理与附件存储说明（P3）](#13-图片处理与附件存储说明p3)
14. [统计与报表（P4）](#14-统计与报表p4)

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
361 passed in 243.11s
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

每个阶段都有自己的验收入口，按需选用：

```powershell
# P1 底座
.\.venv\Scripts\python.exe scripts\verify_stage1.py

# P2 比赛与任务
.\.venv\Scripts\python.exe scripts\verify_stage2.py

# P3 提交、图片处理与审核闭环
.\.venv\Scripts\python.exe scripts\verify_stage3.py

# P4 统计与报表（当前阶段用这个）
.\.venv\Scripts\python.exe scripts\verify_stage4.py
```

`verify_stage4.py` 会依次检查：Python 与依赖（含 openpyxl 版本锁定）、Django 配置、
迁移状态、PostgreSQL 连接、reports 应用与模块、路由挂载、
**核心口径实测（版本不重复计入记录数）**、**权限隔离实测**、
**P4 测试（47 项）**、**P1/P2/P3 回归测试（52 + 141 + 121 项，确保没有退化）**、
全套测试、PostgreSQL 专项、既有文件未被覆盖、agent-loop 护栏未被修改、
未越界实现贡献积分、README 一致性。

- **退出码 0** → 全部必要检查通过
- **退出码 1** → 有检查失败，看输出的「失败项」
- 标记为 ⏭️ 的是**环境阻塞**（例如本机没装 Docker），**不计入通过**，
  会单独列在「未验证事项」里

结果同时写入对应的 `verify_stageN_report.json`，可留档核对。

> ⚠️ 注意：验收入口里的「核心口径」与「权限隔离」两项是**独立复验**的 ——
> 它们不依赖测试文件的写法，即使测试被误改也能拦住。这是刻意的设计。

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
│   │                              0002_seed_p2_roles.py ← P2 角色种子
│   └── templates/accounts/       登录页、改密页
│
├── core/                         公共基础
│   ├── views.py                  首页 + 健康检查 + P3 私有附件下载
│   ├── urls.py
│   ├── context_processors.py
│   ├── models.py                 AuditLog 操作记录（P2）
│   ├── audit.py                  审计写入辅助
│   ├── permissions.py            角色判定（P2）
│   ├── imaging.py                ★P3 图片处理纯逻辑（魔数+解码校验/EXIF/白底/压缩/缩略图）
│   ├── upload_config.py          ★P3 上传与配额上限（集中配置）
│   ├── storage.py                ★P3 私有存储适配层（Local / COS + 预签名）
│   ├── templates/core/home.html  首页模板
│   └── static/core/              Bootstrap 5 与站点样式
│
├── competitions/                 比赛管理（P2）
│   ├── models.py                 Competition / ReviewerAssignment
│   ├── permissions.py            对象级权限（管理权与审核权分离）
│   ├── forms.py / views.py / urls.py
│   └── templates/competitions/
│
├── tasks/                        任务与分配（P2）
│   ├── models.py                 WorkType / TaskTemplate / CompetitionTask / TaskAssignment
│   ├── permissions.py / forms.py / views.py
│   ├── urls.py（干事端）  urls_admin.py（管理端）
│   └── templates/tasks/
│
├── submissions/                  工作成果提交（P3）
│   ├── models.py                 ★Submission / SubmissionVersion / Attachment
│   │                              + UserImageUsage（配额）/ OrphanObject（待清理）
│   ├── services.py               ★提交与配额的服务层（行级锁、版本分配、审核状态机）
│   ├── pipeline.py               ★上传管线（校验/处理/存储/失败补偿）
│   ├── orchestration.py          ★事务编排（先上传后写库、失败回删对象）
│   ├── permissions.py            附件访问与审核权限
│   ├── forms.py / views.py
│   ├── urls.py（干事端+审核端）  urls_admin.py（管理端）
│   └── templates/submissions/
│
├── reviews/                      审核（P3）
│   ├── models.py                 ★ReviewLog（不可覆盖的审核历史）
│   └── migrations/
│
├── templates/base.html           全站基础模板
├── tests/                        自动化测试
│   ├── conftest.py               P1 公共 fixture
│   ├── test_accounts.py          用户模型与认证
│   ├── test_password_change.py   首登强制改密
│   ├── test_health.py            健康检查
│   ├── test_settings.py          生产配置安全约束
│   ├── test_database.py          PostgreSQL 连接与迁移
│   ├── p2/                       P2 测试（模型/权限/页面）
│   └── p3/                       ★P3 测试（图片/配额上传/提交审核）
│       ├── test_imaging.py           图片纯逻辑 36 项
│       ├── test_quota_and_upload.py  配额、并发、失败补偿、私有存储 26 项
│       └── test_submission_review.py 提交、版本、审核闭环 35 项
│
├── scripts/
│   ├── verify_stage1.py          P1 统一验证入口
│   ├── verify_stage2.py          P2 统一验证入口
│   ├── verify_stage3.py          ★P3 统一验证入口
│   ├── setup_p3_loop.py          ★建立 P3 专用 goal / verify / 基线
│   ├── check_prod_wsgi.py        生产 WSGI 入口冒烟检查
│   ├── check_member_isolation.py 干事端隔离实测（真实 HTTP 会话）
│   ├── seed_demo_data.py         演示数据（--reset 可清理）
│   └── p3_baseline.json          ★P3 开工时的 P1/P2 文件哈希基线
│
├── p3-loop/                      ★P3 专用循环配置（目标/闸门/基线）
│   ├── goal.json  verify.json  baseline.json
│
├── private_media/                ★本地私有附件（已 gitignore，不进仓库）
│
└── deploy/
    ├── gunicorn.conf.py          生产应用服务器配置
    └── nginx/pingpong.conf       Nginx 反向代理配置
```

### 关键文件速查

| 文件 | 作用 | 改它的时机 |
|---|---|---|
| `config/settings/base.py` | 公共配置、已安装应用、上传上限 | 新增业务模块时 |
| `config/settings/prod.py` | 生产安全配置（强制 COS 存储） | 一般不用改 |
| `accounts/models.py` | 用户与角色模型 | 要加用户字段时 |
| `core/imaging.py` | **图片处理纯逻辑** | 调整压缩参数或支持新格式时 |
| `core/upload_config.py` | **上传与配额上限** | 调整限制时（改这里或 `.env`） |
| `core/storage.py` | **私有存储适配层** | 更换存储后端时 |
| `submissions/services.py` | **提交/配额/审核的事务逻辑** | 改业务规则时（注意行级锁） |
| `submissions/pipeline.py` | **上传管线与失败补偿** | 调整校验或处理流程时 |
| `submissions/orchestration.py` | **事务编排** | 调整事务边界时 |
| `deploy/nginx/pingpong.conf` | Nginx 配置（含请求体上限） | 换域名/调整大文件上限时 |
| `requirements.txt` | 依赖清单（已锁定版本） | 增删依赖时 |
| `.env.example` | 环境变量样例 | 新增环境变量时 |
| `scripts/verify_stage3.py` | **P3 统一验证入口** | 新增检查项时 |

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

## 12. 提交与审核操作指南（P3）

> 全部在浏览器里完成，**不需要**命令行或数据库工具。

### 12.1 干事：提交工作成果

1. 顶部导航点「**我的工作记录**」，或访问 `/my/submissions/`
2. 上方会列出「可以提交的任务」，显示 `已提交 / 上限`
   - 已达上限时显示「已达上限」，不能再新建（但被退回的可以改）
3. 点「**提交成果**」进入表单：
   - **文字说明**：描述做了什么
   - **图片证明**：可多选；**手机上会直接唤起相机拍照**
   - 文件 / 视频 / 作品链接：只有任务模板允许时才出现
4. 点「提交」→ 状态变为「**待审核**」

**注意事项**：
- 每项材料的上限（如单张图片 10 MiB）会写在输入框下方
- 逾期的任务仍可提交，但会被标记「**逾期**」
- 提交后**原始未处理的大图不会被长期保留**（见第 13 节）

### 12.2 干事：查看结果与修改重提

在「我的工作记录」里能看到每条记录的状态：

| 状态 | 含义 | 可以做什么 |
|---|---|---|
| 待审核 | 已提交，等审核人处理 | 等待 |
| **退回修改** | 需要改进 | 点「修改并重新提交」 |
| 审核通过 | 已完成 | 如需复核，等审核人发起重新审核 |

点记录可看到：**每一次提交的版本**（含历史版本的文字与图片）、**审核记录与退回理由**。

> **重要**：修改重提**不会**占用新的提交次数（规则如此），
> 且第 1 版的文字与图片会**完整保留**，不会被覆盖。

### 12.3 审核人：审核

1. 顶部导航点「**待审核记录**」（`/my/submissions/review/`）
   - 只显示**您被授权审核**的比赛下的记录
   - **您自己提交的记录不会出现在这里**（不能审自己的）
2. 点「查看并审核」→ 可看到完整材料（缩略图点开是大图）
3. 选择：
   - **通过**：记录变为「审核通过」
   - **退回修改**：**必须填写理由**，提交人能看到
4. 如果两个人同时审核同一条，后点的会看到「状态刚被他人更新，请刷新」——
   这是并发保护，不会产生两个互相矛盾的结果

### 12.4 重新审核（已通过的记录）

若发现已通过的记录有问题：

1. 在审核页或记录详情点「**发起重新审核**」
2. **必须填写原因**
3. 记录回到「待审核」

> 原来的「通过」记录与全部历史**都会保留**，不会被删除或覆盖。

### 12.5 移动端

手机浏览器可以直接用：拍照/选图、填写文字、上传文件都正常。
按钮已按触控尺寸放大，窄屏不会横向溢出。

---

## 13. 图片处理与附件存储说明（P3）

### 13.1 图片会被自动处理

上传的图片**不会原样保存**，系统会自动：

| 步骤 | 说明 |
|---|---|
| 格式校验 | 依据**文件真实内容**判断（不只看扩展名），支持 JPEG / PNG / WebP |
| 方向修正 | 手机竖拍照片按 EXIF 摆正，不会躺倒 |
| 透明处理 | 透明 PNG 转 JPEG 时先垫**白底**，不会变黑 |
| 主图压缩 | 长边压到 **1280** 像素、JPEG 质量 82，长宽比不变 |
| 缩略图 | 额外生成一张 **400×400** 的方形缩略图（居中裁切，不拉伸），列表用它加载 |

处理后的文件名是**服务器生成的随机名**（如 `a1b2c3….jpg`），
不使用你上传的原始文件名作为存储路径。

> ⚠️ **原始未处理的大图不会长期保留**。这样做是为了节省存储与配额：
> 手机原照动辄几 MB，保留原图既占空间又拖慢页面。

### 13.2 个人图片配额

| 项目 | 默认上限 | 说明 |
|---|---|---|
| 图片张数 | **50 张** | 每名干事**全局累计**（跨比赛、跨任务） |
| 图片存储量 | **100 MiB** | 按**处理后**的主图 + 缩略图实际大小计算 |

- 一次上传只算 **1 张**（不会因为存了主图和缩略图就算 2 张）
- 历史版本里的图片**照样计入** —— 不能靠删旧版本规避
- 上传失败**不会**占用配额
- 上限可在 `.env` 里调整（`UPLOAD_IMAGES_PER_USER` / `UPLOAD_IMAGE_BYTES_PER_USER`）

### 13.3 附件是私有的

- 附件**不能**通过 `/static/` 或 `/media/` 直接访问
- 每次下载都会生成一个**短时有效**的链接（默认 **5 分钟**），过期后失效
- 只能看到**自己**的附件；审核人能看被授权比赛里的附件；其他人一律拒绝

### 13.4 存储位置

| 环境 | 存储 | 配置 |
|---|---|---|
| 本地开发 | 服务器私有目录 | `PRIVATE_STORAGE_BACKEND=local`（默认） |
| **生产** | **腾讯云 COS 私有存储** | `PRIVATE_STORAGE_BACKEND=cos` + COS 凭据 |

生产环境**必须**配置 COS：如果缺配置，系统会**直接报错**，
不会偷偷把附件写到服务器磁盘上（那会让"私有存储"形同虚设）。

### 13.5 上传失败时的清理

COS 不在数据库事务里，因此「文件已上传、但写库失败」时需要把文件删掉。
如果**删除也失败**（网络抖动等），系统会把该文件记进「待清理」名单 ——
**不会假装已经清理**。管理员可随时查看与重试：

```powershell
# 先看看有哪些待清理（不删除任何东西）
.\.venv\Scripts\python.exe manage.py cleanup_orphans --dry-run

# 真正重试删除
.\.venv\Scripts\python.exe manage.py cleanup_orphans

# 只处理 7 天前的
.\.venv\Scripts\python.exe manage.py cleanup_orphans --older-than 7
```

> 命令会自动跳过**仍被正常记录引用**的文件，不会误删有效附件。
> 管理端「工作记录管理」页顶部也会提示待清理数量。

#### 配置 COS 的步骤

1. 在腾讯云创建**私有读写**的 Bucket（不要开公有读）
2. 建议用**子账号**并只授予该 Bucket 的读写权限，不要用主账号密钥
3. 在 `.env` 里填：
   ```ini
   PRIVATE_STORAGE_BACKEND=cos
   COS_REGION=ap-beijing
   COS_BUCKET=your-bucket-1250000000
   COS_SECRET_ID=你的子账号SecretId
   COS_SECRET_KEY=你的子账号SecretKey
   ```
4. 重启服务

> ⚠️ **本机未配置真实 COS 凭据，因此生产 COS 链路尚未实测。**
> 详见文末「已验证 / 未验证事项」。

---

## 14. 统计与报表（P4）

> 全部在浏览器里完成：**打开页面 → 选筛选条件 → 点导出**。

### 14.1 怎么用

1. 顶部导航点「**统计报表**」（或访问 `/reports/`）
2. 按需选择筛选条件（可留空 = 全部）
3. 点「**导出 Excel**」下载报表

### 14.2 报表内容（三个工作表）

导出的 Excel 里有**三个工作表**，口径完全一致，可以互相核对：

| 工作表 | 内容 |
|---|---|
| **按人员汇总** | 每名干事：工作记录数、提交版本数、各状态数、逾期数、图片/文件/视频/链接数 |
| **按工作类型汇总** | 每个工作类型：工作记录数、版本数、各状态数、参与人数、涉及任务数与比赛数 |
| **工作记录明细** | 每条工作记录一行：比赛、任务、类型、成员、状态、版本、时间、最近审核结果与意见 |

每张表的顶部会写明**筛选条件**与**统计口径**，便于转发给他人时不被误读。

### 14.3 筛选条件

| 条件 | 说明 |
|---|---|
| 比赛 / 任务 / 工作类型 | 下拉选择，只列出**你有权看到**的范围 |
| 成员 | 按人筛选 |
| 状态 | 待审核 / 退回修改 / 审核通过 |
| 起始日期 / 结束日期 | 按**记录创建时间**筛，结束日期**含当天整天** |
| 只看逾期提交 | 勾选后只列逾期记录 |

> 如果填了无效值（例如日期格式不对），页面会**明确提示"已忽略该条件"**，
> 不会静默忽略让你以为筛选生效了。

### 14.4 一个重要口径：记录数 ≠ 版本数

**「工作记录数」按记录计，一条记录被退回重提多次仍只算 1 条。**

举例：某干事交了一张海报，被退回改了 2 次 → 这是 **1 条工作记录**、**3 个提交版本**。

- 表里两个数字**分别列出**，便于交叉核对
- 三张表的记录数**加总必然相等**（按人员合计 = 按类型合计 = 明细行数）——这是刻意设计的自检
- 明细表**按记录出行**，多版本记录只占一行，版本号在行内显示为「第 3 版 / 共 3 版」

### 14.5 权限

页面与导出**走同一套权限判断**，不存在"页面上看不到、导出却能拿到"：

| 你的身份 | 能看到 |
|---|---|
| 干事 | **只有自己的**工作记录 |
| 比赛授权审核人 | 自己被授权审核的比赛下的记录 |
| 社团管理员 | 全部 |

> 即使手工改网址里的筛选参数（例如换成别人的 ID），**也不会**拿到别人的数据 ——
> 权限是落在查询层面的，不是靠页面隐藏。

### 14.6 报表里没有的东西

- **贡献积分**：本阶段**未实现**（积分规则尚未确定）
- 积分排名、Excel 汇总报表以外的统计图表

---

## 附：本阶段明确不做的事

- 贡献积分、Excel 汇总报表、积分排名（属于 P4 及以后）
- 公开自助注册（账号一律由管理员创建）
- 创建云服务器、购买域名、支付云资源费用
- 比赛胜负、比分、球员出场、单双打或裁判判罚（**不属于本项目范围**）
- 宿主机 systemd / 裸机部署脚本（本项目已选定 Docker Compose 路线）

---

## 附：本阶段已验证 / 未验证事项

> 这里如实区分「已经真实验证过」和「因环境限制没验证」。**后者不算通过。**

### P1 / P2 既有能力（回归保持）

| 项目 | 验证方式 | 结果 |
|------|----------|------|
| Python / Django / PostgreSQL | 运行时读取与实连 | 3.13.15 / 5.2.18 / PostgreSQL 18.1 |
| P1 测试 | `pytest` | **52 项通过**（未退化） |
| P2 测试 | `pytest` | **141 项通过**（未退化） |
| 健康检查 | 真实 HTTP 请求 | `/health/live`、`/health/ready` 均 200 |
| 生产 WSGI 入口 | 以 WSGI 协议调用 `config.wsgi:application` | 首页、登录页、健康检查、静态资源均 200 |
| agent-loop 护栏 | 比对文件哈希 | 10 个文件与任务开始时一致，未被修改 |

### P3 新增能力（本阶段实测）

| 项目 | 验证方式 | 结果 |
|------|----------|------|
| P3 测试 | `pytest tests/p3` | **97 项全部通过** |
| 全套测试 | `pytest` | **290 项通过**（52 + 141 + 97） |
| 图片格式检测 | 真实解码校验 | JPEG / PNG / WebP 识别；GIF、BMP 被拒 |
| 伪造图片 | 拼接/截断/乱码输入 | 全部被拒且抛可预期异常，**不产生 500** |
| EXIF 方向修正 | 造 Orientation=6 的图 | 800×400 → 400×800，方向正确 |
| 透明转白底 | 全透明 PNG 中心画红块 | 透明区域 RGB=(255,255,255)，红块保留 |
| 主图压缩 | 2400×1600 输入 | 输出 1280×853，长宽比保持 |
| 缩略图 | 800×200（红/绿/蓝）输入 | **恰好 400×400**，中心与边缘均为绿色 → 确为居中裁切非拉伸 |
| 对象键 | 生成 20 次并正则校验 | 全部为 `<32位uuid>.jpg` / `_thumb.jpg`，不含用户输入 |
| 提交必填校验 | 缺文字 / 缺图片 | 均被拒并给出缺失项名称 |
| **并发提交不超额** | 6 线程并发提交（上限 1） | 成功 1 / 被拒 5，实际记录数 = 1 |
| **并发审核只一个结果** | 2 线程同时通过 | 成功 1，审核记录仅 1 条 |
| 重提不占新增次数 | 退回后重提 | 工作记录数不变，版本号 1 → 2 |
| 旧版本保全 | 对比 v1/v2 附件 ID | 不重叠，v1 文字未被覆盖 |
| 配额记账一致 | 与附件重算对比 | `image_count` / `total_bytes` 完全一致 |
| 配额按处理后字节 | 检查主图记录 | `thumbnail_bytes` 计入，缩略图不重复计张 |
| 退回必填理由 | 服务层 + 数据库双重 | 两层都拒绝空理由 |
| 重新审核保留历史 | 通过→重新审核 | 原通过记录与原意见完整保留 |
| 归档后行为 | 归档比赛 | 禁新增；既有记录可审核、可修改 |
| 附件权限 | 匿名 / 跨用户 / 本人 / 审核人 | 前三者按规则拒绝，审核人可看被授权比赛的 |
| 预签名链接 | 篡改 + 等待过期 | 篡改无效；超期后失效 |
| 生产存储 fail-closed | 空后端 / 缺凭据 | 均明确报错，**不回退本地** |
| Dockerfile Pillow 依赖 | **解析 wheel 的 ELF DT_NEEDED** | `_imaging.so` 依赖 `libz.so.1`（wheel 未自带）→ 装 `zlib1g`；`libjpeg` 等 wheel 自带 → **未盲目装 `libjpeg62-turbo`** |
| Nginx 请求体上限 | 解析配置值 | `320m` ≥ 300 MiB 视频上限 |
| 验证入口退出码 | 正常跑（见下） | 正常 `0` |

### 未验证（环境限制，**不代表通过**）

| 项目 | 原因 | 已做的替代验证 |
|------|------|----------------|
| **真实 COS 生产链路** | 本机**未提供腾讯云 COS 凭据** | 本地私有存储适配器 + 预签名签名/过期机制已实测；COS 分支用**模拟适配器**测试（`test_quota_and_upload.py`），未消耗真实资源。配置步骤与权限最小化建议已写入第 13.4 节 |
| **Docker 镜像构建与容器启动** | 本机**未安装 Docker**（无管理员权限、winget 不可用） | 对 `Dockerfile` 的 Pillow 依赖做了 **wheel ELF 依赖分析**（比"文本检查"更强的依据）；compose 结构已校验 |
| Gunicorn 实际运行 | 依赖 Unix 专有的 `fcntl`，**Windows 无法运行** | 以 WSGI 协议直接调用同一个 `config.wsgi:application` |
| Nginx / HTTPS 真实流量 | 无 Linux/容器环境，且本阶段不购买域名 | 配置已交付并与 compose 服务名对齐 |
| **300 MiB 大文件端到端上传** | 受本机与无容器环境限制 | 实现层面已确保**不整读进内存**（Django 临时文件 + COS 分块 `upload_file`），并对单文件上限做了配置化校验；未在真实生产形态跑通完整链路 |

**若要在 Linux 或装有 Docker 的机器上补完这些验证：**

```bash
# 1. 容器构建与启动（同时验证 Pillow 在容器内可用）
docker compose build
docker compose up -d
docker compose ps
curl -I http://localhost/health/ready

# 2. 在容器内实际跑一次图片处理（确认 zlib 生效）
docker compose exec app python -c "
from core.imaging import process_image
from tests.p3.conftest import make_image_bytes
r = process_image(make_image_bytes())
print('主图', r.main_width, r.main_height, '缩略图', r.thumb_width, r.thumb_height)
"

# 3. 配置真实 COS 后跑 P3 验收（会自动执行 COS 往返测试）
export COS_REGION=... COS_BUCKET=... COS_SECRET_ID=... COS_SECRET_KEY=...
python scripts/verify_stage3.py --only cos

# 4. 大文件上传（准备一个 ~300MB 视频，通过页面提交）
```

---

## 附：下一阶段（P4）开始前需要确定的事项

以下是实现**贡献积分与 Excel 报表**之前必须先定下来的业务规则。
不提前替社团做决定，先列出来供讨论：

1. **积分怎么算**
   按"每通过一条工作记录记 N 分"？还是按工作类型区分（海报 5 分、推文 3 分）？
   同一个任务里多交几条是否都计分？被退回后重提通过的怎么算？

2. **积分是否可人工调整**
   谁有权加/减分？必须填写原因吗？调整记录要不要进审核历史？

3. **积分的时间口径**
   按学期累计还是自然年？跨学期是否清零？历史积分是否需要可追溯（按当时规则算还是按当前规则重算）？

4. **Excel 报表的范围**
   导出哪些表 —— 成员积分明细？比赛参与统计？任务完成情况？
   社团以前用过固定的表格模板吗（如果有，请提供，可以照着生成）？

5. **报表的口径**
   谁可以导出（管理员？还是所有人都能看自己的）？导出全量还是按比赛筛选？

6. **是否需要历史数据导入**
   社团现有的 Excel/纸质记录要不要导进系统？如果导，积分从哪一天开始算？

7. **"工作统计"的最终形态**
   目前已有比赛、任务、提交与审核数据。社团最想看到的是哪种视图 ——
   按人看（某人这学期做了什么）？按比赛看（这站比赛谁贡献了什么）？两者都要？
