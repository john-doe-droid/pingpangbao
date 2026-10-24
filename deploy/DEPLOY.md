# 部署手册

中国农业大学乒乓球社工作统计网站。本文是从一台**全新云服务器**到**网站可访问**的完整步骤，
照着从上往下执行即可。每一步都给出「怎么验证这步成功了」，不要跳过验证。

> 使用 Azure 时，请先按 [`AZURE.md`](AZURE.md) 选择虚拟机、磁盘和网络规格，再回到本文部署。

- 目标环境：Ubuntu 22.04 / 24.04 LTS，1 核 2G 起（图片处理建议 2 核 4G）
- 技术栈：Django 5.2 + PostgreSQL 18 + Gunicorn + Nginx，全部跑在 Docker Compose 里
- 数据位置：**数据库与附件都存放在宿主机的一个数据目录里**，备份/迁移只处理它

---

## 0. 你需要先准备什么

| 项目 | 说明 | 是否必须 |
|---|---|---|
| 云服务器 | 1 核 2G 起，Ubuntu 22.04+，有公网 IP | 必须 |
| 域名 | 解析到服务器 IP 的 A 记录，如 `pingpong.example.com` | 必须（用 IP 也能跑，但没 HTTPS） |
| SSH 登录方式 | 密钥或密码，能 `sudo` | 必须 |
| 备案 | 服务器在中国大陆时，域名需完成 ICP 备案 | 大陆服务器必须 |

> 域名解析生效检查：`ping 你的域名`，应返回你的服务器公网 IP。

---

## 1. 服务器初始化

### 1.1 登录并更新系统

```bash
ssh root@你的服务器IP

apt update && apt upgrade -y
apt install -y curl git ufw
```

### 1.2 创建专用用户（不建议长期用 root 跑服务）

```bash
adduser --disabled-password --gecos "" pingpong
usermod -aG sudo pingpong
mkdir -p /home/pingpong/.ssh
cp ~/.ssh/authorized_keys /home/pingpong/.ssh/ 2>/dev/null || true
chown -R pingpong:pingpong /home/pingpong/.ssh
chmod 700 /home/pingpong/.ssh
chmod 600 /home/pingpong/.ssh/authorized_keys 2>/dev/null || true
```

**验证**：另开一个终端 `ssh pingpong@你的服务器IP` 能登录，再继续。别关掉当前 root 会话。

### 1.3 安装 Docker

```bash
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
usermod -aG docker pingpong
```

**验证**：

```bash
docker --version            # 显示版本号
docker compose version      # 显示 v2.x
```

> `usermod` 后需要重新登录才生效。重新 `ssh pingpong@IP` 后 `docker ps` 不报权限错误即为成功。

### 1.4 配置防火墙

```bash
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status
```

**验证**：`ufw status` 显示 22 / 80 / 443 为 ALLOW，其余为 deny。
**注意**：数据库端口 5432 和应用端口 8000 **不要**对外开放——它们只在 Docker 内部网络里。

---

## 2. 创建数据目录

这是本项目最关键的一步：**数据库和用户附件都放在这个目录里**。
只要这个目录在，换服务器、重装系统都不会丢数据。

```bash
sudo mkdir -p /srv/pingpong-data/{postgres,private_media}
```

> **PostgreSQL 18 挂载路径**：本项目的 compose 把数据目录挂到
> `/var/lib/postgresql`。不要改回旧版的 `/var/lib/postgresql/data`；
> PostgreSQL 18 的新数据目录在 `/var/lib/postgresql/18/docker`，
> 旧挂载点不会用于保存数据库。

### 2.1 设置目录属主（否则容器写不进去）

容器里的应用以 UID **1001** 运行，PostgreSQL 容器以 UID **999** 运行。
目录属主不对，会出现「附件上传失败」「数据库起不来」这类很难查的问题。

```bash
sudo chown -R 999:999  /srv/pingpong-data/postgres
sudo chown -R 1001:1001 /srv/pingpong-data/private_media
sudo chmod 750 /srv/pingpong-data/postgres /srv/pingpong-data/private_media
```

**验证**：

```bash
ls -ln /srv/pingpong-data
# postgres 应显示 999 999，private_media 应显示 1001 1001
```

> 为什么不用 Docker 具名卷：具名卷藏在 `/var/lib/docker/volumes/` 里，
> 备份和迁移都得绕一层命令。用宿主机目录后，`tar` 打包即可备份，
> 复制到新机器即可迁移。代价就是上面这几行授权。

---

## 3. 获取代码

```bash
sudo mkdir -p /srv/pingpong && sudo chown pingpong:pingpong /srv/pingpong
git clone git@github.com:john-doe-droid/pingpangbao.git /srv/pingpong/app
cd /srv/pingpong/app
```

如果服务器上没有配 SSH key，改用 HTTPS：

```bash
git clone https://github.com/john-doe-droid/pingpangbao.git /srv/pingpong/app
```

**验证**：`ls` 能看到 `manage.py`、`docker-compose.yml`、`deploy/`。

---

## 4. 写生产配置 `.env`

`.env` **不进仓库**（已在 `.gitignore` 里），必须在服务器上手工创建。

```bash
cd /srv/pingpong/app
cp .env.example .env
```

先生成密钥：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

然后编辑 `nano .env`，逐项填好：

### 4.1 必填项（缺一项都起不来）

```ini
# --- Django 基础 ---
DJANGO_SETTINGS_MODULE=config.settings.prod
DJANGO_SECRET_KEY=<上一步生成的 64 位随机串>
DJANGO_DEBUG=false
DOMAIN=pingpong.example.com
DJANGO_ALLOWED_HOSTS=pingpong.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://pingpong.example.com
DJANGO_HTTPS_ENABLED=true

# --- 数据库（服务名 db 由 compose 提供，不要改成 127.0.0.1）---
POSTGRES_DB=pingpong
POSTGRES_USER=pingpong
POSTGRES_PASSWORD=<自己设一个强密码，别用默认值>
POSTGRES_HOST=db
POSTGRES_PORT=5432

# --- 私有附件存储（P5：默认本地磁盘）---
PRIVATE_STORAGE_BACKEND=local
PRIVATE_MEDIA_ROOT=/data/private_media

# --- 宿主机数据目录（compose 用它做 bind mount）---
PINGPONG_DATA_DIR=/srv/pingpong-data
```

### 4.2 各项为什么这么填

| 变量 | 说明 |
|---|---|
| `DJANGO_SECRET_KEY` | 签名、会话加密都靠它。**少于 40 字符会直接报错**（这是刻意的）。换掉它会让所有人登录态失效。 |
| `DOMAIN` | 生产裸域名。Nginx 模板和证书路径会自动使用它，不要写 `https://` 或路径。 |
| `DJANGO_ALLOWED_HOSTS` | 只填域名，不要带 `https://`。填错的典型症状是访问出现 `DisallowedHost`。 |
| `POSTGRES_HOST` | 必须是 `db`（compose 里的服务名）。写 `127.0.0.1` 会连不上——数据库在另一个容器里。 |
| `PRIVATE_MEDIA_ROOT` | **容器内**路径，固定写 `/data/private_media`，由 compose 映射到宿主机目录。 |
| `PINGPONG_DATA_DIR` | **宿主机**路径。它和上面的 `/data/private_media` 是同一个目录的两端。 |

> **两个路径别搞混**：`PINGPONG_DATA_DIR` 是宿主机（服务器）上的路径；
> `PRIVATE_MEDIA_ROOT` 是容器内部的路径。两者通过 `docker-compose.yml` 的
> `- ${PINGPONG_DATA_DIR}/private_media:/data/private_media` 对应起来。

### 4.3 可选：改用腾讯云 COS

如果日后要多机部署或附件量很大，可以切到对象存储：

```ini
PRIVATE_STORAGE_BACKEND=cos
COS_REGION=ap-beijing
COS_BUCKET=你的bucket名-1250000000
COS_SECRET_ID=...
COS_SECRET_KEY=...
COS_SCHEME=https
```

四项缺任意一项都会在启动时报错（不会悄悄退回本地磁盘）。Bucket 必须是**私有读写**。

### 4.4 保护配置文件

```bash
chmod 600 .env
```

**验证**：

```bash
grep -c . .env          # 有内容
ls -l .env              # 权限应为 -rw-------
```

### 4.5 一个容易踩的坑：不要照抄本地 `.env`

`config/settings/base.py` 会主动读取仓库根目录的 `.env`（`load_dotenv`），
**不管用的是 dev 还是 prod 配置**。这意味着：

> 如果你把本机开发用的 `.env` 直接拷到服务器，那些开发值会**覆盖**生产配置，
> 把本该打开的安全开关关掉 —— 而且不会有任何报错。

最典型的是这两项：

| 变量 | 开发值 | 必须的生产值 | 配错的后果 |
|---|---|---|---|
| `DJANGO_HTTPS_ENABLED` | `false` | `true` | 关闭 HSTS 与 HTTPS 跳转，流量可能走明文 |
| `DJANGO_DEBUG` | `true` | `false` | 报错页会暴露源码片段与配置细节 |

**所以服务器上的 `.env` 必须从 `.env.example` 重新生成，不要从本机复制。**

配好后用这条命令核对（它会真正加载生产配置并打印关键开关）：

```bash
cd /srv/pingpong/app
docker compose exec app python -c "
import django, os
django.setup()
from django.conf import settings
print('DEBUG          =', settings.DEBUG)
print('HTTPS 跳转     =', settings.SECURE_SSL_REDIRECT)
print('HSTS 秒数      =', settings.SECURE_HSTS_SECONDS)
print('存储后端       =', settings.PRIVATE_STORAGE_BACKEND)
print('数据目录       =', settings.PRIVATE_MEDIA_ROOT)
"
```

**期望结果**：

```
DEBUG          = False
HTTPS 跳转     = True
HSTS 秒数      = 31536000
存储后端       = local
数据目录       = /data/private_media
```

出现 `DEBUG = True` 或 `HTTPS 跳转 = False`，说明 `.env` 里残留了开发值，回去改掉再重启。

---

## 5. 配置域名与 HTTPS

### 5.1 确认域名配置

域名只写在 `.env` 的 `DOMAIN` 中。Nginx 使用官方镜像的模板替换机制生成配置，
不要再手工修改 `deploy/nginx/pingpong.conf`。

```bash
cd /srv/pingpong/app
grep '^DOMAIN=' .env
grep '^DJANGO_ALLOWED_HOSTS=' .env
```

两项都必须是同一个裸域名，例如 `pingpong.example.com`。

### 5.2 首次申请证书

首次申请使用仓库提供的脚本。它会临时启动一个只开放 ACME 验证路径的 Nginx，
拿到证书后自动清理临时容器，因此不会遇到「完整 Nginx 配置缺少证书而无法启动」的问题。
临时站点配置是 `deploy/nginx/acme-bootstrap.conf`。

```bash
cd /srv/pingpong/app
sh deploy/init-certificates.sh pingpong.example.com 你的邮箱
```

把域名和邮箱换成真实值。域名必须已经解析到当前服务器，且 80 端口没有被其他程序占用。

**验证**：

```bash
ls deploy/certbot/conf/live/pingpong.example.com/
pwd  # 必须仍为 /srv/pingpong/app；脚本返回后证书路径是相对它计算的
```

能看到 `fullchain.pem` 和 `privkey.pem` 才算成功。

### 5.3 启动 Nginx 使 HTTPS 生效

```bash
docker compose up -d nginx
docker compose ps
```

**验证**：浏览器打开 `https://你的域名`，证书有效、地址栏有小锁。

> 若 80 端口被占用或证书申请失败，检查：域名是否真的解析到本机、`ufw` 是否放行 80、
> 以及 Nginx 是否在跑。

### 5.4 证书自动续期

先把证书续期脚本加入 cron。脚本只在证书接近到期时真正续期，完成后让 Nginx 重新加载。

```bash
sudo install -d -m 750 -o pingpong -g pingpong /srv/backup
sudo tee /etc/cron.d/pingpong-certbot > /dev/null <<'EOF'
30 3 * * * pingpong cd /srv/pingpong/app && sh deploy/renew-certificates.sh >> /srv/backup/certbot-renew.log 2>&1
EOF
sudo chmod 644 /etc/cron.d/pingpong-certbot
```

**验证**：

```bash
sudo -u pingpong sh deploy/renew-certificates.sh
```

---

## 6. 启动应用

```bash
cd /srv/pingpong/app
docker compose up -d --build
```

首次会构建镜像，约 3–8 分钟。构建阶段和容器启动时都会执行 `collectstatic`；
启动时执行是为了让持久化静态卷在升级后拿到新的哈希文件。

### 6.1 容器启动时会自动做什么

`app` 容器的启动命令是：

```
python manage.py collectstatic --noinput && python manage.py migrate --noinput && exec gunicorn config.wsgi:application -c deploy/gunicorn.conf.py
```

也就是**先跑数据库迁移，再启动 Gunicorn**。迁移是幂等的，重复启动安全。

### 6.2 验证启动成功

```bash
docker compose ps
```

三个服务都应是 `Up`，`db` 显示 `(healthy)`。

```bash
docker compose logs -f app
```

看到 `Gunicorn 正在启动` 且没有 Traceback 即为正常。`Ctrl+C` 退出日志查看（不影响容器）。

### 6.3 健康检查

```bash
curl -i http://127.0.0.1/health/live     # 200，进程活着
curl -i http://127.0.0.1/health/ready    # 200，且数据库连得上
```

**这两个端点的区别很重要**：

- `/health/live` 只表示进程活着，**不检查数据库**；
- `/health/ready` 会真的查一次数据库。数据库挂了它会返回 503。

排查时先看 `ready`——它挂了一般是数据库问题。

### 6.4 创建管理员账号

```bash
docker compose exec app python manage.py createsuperuser
```

按提示输入用户名、邮箱、密码。密码别用弱口令——这是社团后台的最高权限。

**验证**：访问 `https://你的域名/admin/`，用刚建的账号能登录。

---

## 7. 首次使用前的准备

用刚创建的管理员登录网站，在「管理」中按顺序配置：

1. **工作类型**：定义社团的工作分类，如「场地预约」「器材维护」
2. **比赛**：新建一届比赛并开始进行
3. **任务模板**：为比赛定义任务项
4. **成员**：创建干事账号
5. **比赛详情 → 任务与人员**：创建比赛任务并分配人员

Django `/admin/` 只用于账号、角色和应急运维，日常业务配置全部在网站「管理」页面完成。

> 顺序不能反：任务模板依赖比赛，任务分配依赖模板和成员。

---

## 8. 日常运维

### 8.1 查看日志

```bash
docker compose logs -f app        # 应用日志（含 Gunicorn 输出）
docker compose logs -f nginx      # 访问日志、错误日志
docker compose logs --tail=200 db # 数据库日志
```

应用自身的业务日志也写在容器 `/app/logs`，对应具名卷 `applogs`。

### 8.2 重启 / 停止

```bash
docker compose restart app     # 只重启应用（改代码后）
docker compose down            # 停止全部（数据仍在数据目录里，不会丢）
docker compose up -d           # 重新启动
```

**不要**用 `docker compose down -v`——`-v` 会删掉具名卷。虽然数据库和附件已改为
宿主机目录（不受影响），但 `static` / `applogs` 卷会被清掉。

### 8.3 更新代码

```bash
cd /srv/pingpong/app
git pull
docker compose up -d --build     # 重建镜像，启动时自动跑迁移
```

**更新前先备份**（见第 9 节）。迁移一旦执行，回退旧版本可能需要手工处理数据。

### 8.4 设置开机自启

`docker-compose.yml` 里每个服务都写了 `restart: unless-stopped`，
Docker 服务本身已 `systemctl enable`，因此服务器重启后容器会自动起来。

**验证**：`sudo reboot` 后等 1 分钟，`docker compose ps` 三个服务都应 `Up`。

---

## 9. 备份与恢复

**核心原则：只备份 `/srv/pingpong-data` 这一个目录，数据库和附件就都在里面了。**

### 9.1 备份

仓库提供了备份脚本。它使用 `pg_dump` 做逻辑备份、打包附件，并在成功后才生成
最终文件；任何一步失败都会保留错误退出码，不会用空文件假装备份成功。

```bash
sudo install -d -m 750 -o pingpong -g pingpong /srv/backup
cd /srv/pingpong/app
sudo -u pingpong sh deploy/backup.sh

# 配置文件含密钥，单独备份并严格限制权限
sudo cp /srv/pingpong/app/.env /srv/backup/env_$(date +%F).bak
sudo chmod 600 /srv/backup/env_*.bak
```

**验证备份可用**（很重要，别只备份不验证）：

```bash
ls -lh /srv/backup/
# dump 文件应有合理大小；0 字节说明备份失败
docker compose exec -T db pg_restore -l < "$(ls -1t /srv/backup/db_*.dump | head -n 1)" | head
# 能列出内容清单即为有效备份
```

### 9.2 定时备份（可选但推荐）

新建 `/etc/cron.d/pingpong-backup`：

```bash
sudo tee /etc/cron.d/pingpong-backup > /dev/null <<'EOF'
# 每天凌晨 3:20 备份
20 3 * * * pingpong cd /srv/pingpong/app && sh deploy/backup.sh >> /srv/backup/backup.log 2>&1
EOF
sudo chmod 644 /etc/cron.d/pingpong-backup
```

备份脚本默认保留 30 天，可通过环境变量 `BACKUP_RETENTION_DAYS` 调整。
`/srv/backup` 必须位于另一块磁盘或定期同步到其他机器/网盘，否则服务器磁盘损坏时
数据库、附件和备份会一起丢失。

### 9.3 恢复

```bash
cd /srv/pingpong/app

# 1) 停应用（保留数据库容器）
docker compose stop app

# 2) 恢复数据库
docker compose exec -T db pg_restore -U pingpong -d pingpong --clean --if-exists \
  < /srv/backup/db_2026-10-20.dump

# 3) 恢复附件
sudo rm -rf /srv/pingpong-data/private_media
sudo tar xzf /srv/backup/media_2026-10-20.tar.gz -C /srv/pingpong-data
sudo chown -R 1001:1001 /srv/pingpong-data/private_media

# 4) 启动
docker compose start app
```

**恢复后必须验证**：

```bash
curl -i http://127.0.0.1/health/ready      # 200
```

然后登录网站，随便打开一条带图片的工作记录，**确认图片能显示**——
这一步才能证明附件真的恢复了（数据库里的记录指向对象键，文件得真的在）。

### 9.4 迁移到新服务器

```bash
# 旧机器
sudo tar czf pingpong-full.tar.gz -C /srv pingpong-data
scp pingpong-full.tar.gz 新服务器:/tmp/

# 新机器（已按第 1–4 节装好 Docker 与代码）
sudo tar xzf /tmp/pingpong-full.tar.gz -C /srv
sudo chown -R 999:999  /srv/pingpong-data/postgres
sudo chown -R 1001:1001 /srv/pingpong-data/private_media
cd /srv/pingpong/app && docker compose up -d
```

> 直接搬目录要求 PostgreSQL **大版本一致**（本项目是 18）。
> 跨大版本请改用 9.1 的 `pg_dump` + 9.3 的 `pg_restore`。

---

## 10. 常见故障

### 10.1 应用起不来，日志报 `StorageConfigError`

```
生产环境使用本地磁盘时，必须配置持久化的附件数据目录（PRIVATE_MEDIA_ROOT）
```

`.env` 里没配 `PRIVATE_MEDIA_ROOT`，或还留着开发默认值。按 4.1 补上
`PRIVATE_MEDIA_ROOT=/data/private_media`。

这是**刻意的保护**：不报错的话，附件会被写进容器临时层，容器一重建就全没了。

### 10.2 日志报 `DJANGO_SECRET_KEY 太短`

密钥不足 40 字符。用 4.1 的命令重新生成。注意 `.env` 里不要加引号、不要留空格。

### 10.3 访问网站出现 `DisallowedHost`

`DJANGO_ALLOWED_HOSTS` 里没有当前访问的域名。填**裸域名**，不要带协议和路径。

### 10.4 502 Bad Gateway

Nginx 连不上应用。依次查：

```bash
docker compose ps                    # app 是否 Up
docker compose logs --tail=100 app   # app 是否有 Traceback
curl -i http://127.0.0.1:8000/health/live   # 容器内应用是否响应
```

常见原因是应用启动时迁移失败（数据库没起来）或 `.env` 配错导致进程退出。

### 10.5 数据库连不上 / `ready` 返回 503

```bash
docker compose ps db                  # 是否 healthy
docker compose logs --tail=100 db
```

常见原因：

- `/srv/pingpong-data/postgres` 属主不是 `999:999` → 数据库无法初始化；
- `POSTGRES_PASSWORD` 在数据库初始化**之后**被改过 → 旧密码还在库里，需改回或重建库；
- 磁盘满了 → `df -h`。

### 10.6 图片上传失败

按顺序排查：

1. 目录属主：`ls -ln /srv/pingpong-data/private_media` 应为 `1001 1001`
2. 磁盘空间：`df -h`
3. 文件大小是否超过上限（单图 10MiB，单文件 50MiB，单次提交 100MiB）
4. 应用日志：`docker compose logs --tail=100 app`

### 10.7 上传大文件报 413

Nginx 的 `client_max_body_size` 是 110m。若调大了应用上限，需同步改
`deploy/nginx/pingpong.conf` 并 `docker compose restart nginx`。

### 10.8 附件下载 403 / 链接失效

下载链接是**短时签名**的，默认 300 秒过期。这是有意设计——链接不能被转发给别人长期使用。
刷新页面重新获取即可。若频繁失效，检查服务器时间是否准确：

```bash
timedatectl     # 应显示 NTP synchronized: yes
```

---

## 11. 上线前检查清单

打勾确认，全部通过再对外公布地址：

- [ ] `https://域名` 能打开，证书有效
- [ ] `/health/live` 与 `/health/ready` 都返回 200
- [ ] 管理员能登录 `/admin/`
- [ ] **`DEBUG=False`、`HTTPS 跳转=True`**（见 4.5，别照抄本地 `.env`）
- [ ] 上传一张图片 → 页面能显示 → 点开大图能看
- [ ] 用普通干事账号登录，**看不到**别人的工作记录
- [ ] 上传一个接近 50 MiB 上限的文件不报错
- [ ] `docker compose restart app` 后，刚才上传的图片**仍然能打开**（证明落在持久目录）
- [ ] 重建应用镜像后，页面 CSS/JS 仍能正常加载（证明静态卷已刷新）
- [ ] `sudo -u pingpong sh deploy/backup.sh` 跑通，且 `pg_restore -l` 能列出内容
- [ ] `sudo -u pingpong sh deploy/renew-certificates.sh` 能完成检查并重载 Nginx
- [ ] `/srv/backup` 已同步到服务器之外的另一份存储
- [ ] `ufw status` 里 5432 和 8000 **没有**对公网开放
- [ ] `.env` 权限是 `600`
