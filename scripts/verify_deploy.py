#!/usr/bin/env python
"""部署配置自检 —— 在推代码/上服务器之前跑一遍，抓那些「本地能跑、一部署就炸」的坑。

为什么需要这个脚本：
    P1–P4 的验收入口全部基于 `runserver` + 本地磁盘，天然覆盖不到容器化部署。
    实际已经踩到过的真实缺陷：
      · Dockerfile 只 COPY 了 accounts/core，漏掉 P2–P4 新增的 5 个应用
        → 镜像构建成功，容器启动时 ImportError 直接崩，本地完全测不出来。
      · compose 里附件目录挂载点写错/漏挂 → 附件写进容器临时层，重建即丢。
    这类问题不看代码是发现不了的，所以把检查固化下来。

用法：
    python scripts/verify_deploy.py              # 全部检查
    python scripts/verify_deploy.py -v           # 显示通过项细节

退出码：0 全部通过 / 1 有失败项
"""
from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Report:
    def __init__(self, verbose: bool = False) -> None:
        self.items: list[tuple[str, str, str]] = []
        self.verbose = verbose

    def record(self, name: str, status: str, detail: str = "") -> None:
        self.items.append((name, status, detail))

    def ok(self, name: str, detail: str = "") -> None:
        self.record(name, "PASS", detail)

    def fail(self, name: str, detail: str) -> None:
        self.record(name, "FAIL", detail)

    def skip(self, name: str, detail: str) -> None:
        self.record(name, "SKIP", detail)

    def render(self) -> int:
        mark = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}
        for name, status, detail in self.items:
            if status == "PASS" and not self.verbose:
                print("%s %s" % (mark[status], name))
            else:
                print("%s %s —— %s" % (mark[status], name, detail))

        failed = [i for i in self.items if i[1] == "FAIL"]
        skipped = [i for i in self.items if i[1] == "SKIP"]
        passed = [i for i in self.items if i[1] == "PASS"]
        print()
        print("=" * 78)
        print("汇总：通过 %d 项，失败 %d 项，跳过 %d 项"
              % (len(passed), len(failed), len(skipped)))
        print("=" * 78)
        if skipped:
            print("跳过项（不代表通过）：")
            for name, _, detail in skipped:
                print("  · %s —— %s" % (name, detail))
        if failed:
            print("失败项：")
            for name, _, detail in failed:
                print("  ✗ %s —— %s" % (name, detail))
            return 1
        return 0


# ---------------------------------------------------------------------------
# Dockerfile
# ---------------------------------------------------------------------------

def _installed_app_dirs() -> list[str]:
    """从 base.py 取出本项目自己的应用目录名。

    INSTALLED_APPS 是 `DJANGO_APPS + LOCAL_APPS` 这样的拼接表达式，不是字面量列表，
    直接解析 INSTALLED_APPS 会拿到空列表 —— 本检查第一版就因此"通过"了但实际什么都没查
    （显示"0 个"）。所以这里**递归解析所有 list 字面量赋值**，再按目录存在性筛出本地应用。
    另外加一条兜底：若最终解析结果为空，直接判失败，绝不允许空集合静默通过。
    """
    base = BASE_DIR / "config" / "settings" / "base.py"
    src = base.read_text(encoding="utf-8")
    tree = ast.parse(src)

    candidates: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.List):
            for elt in node.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    candidates.append(elt.value)

    apps = sorted({a for a in candidates
                   if not a.startswith("django.") and (BASE_DIR / a).is_dir()})
    return apps


def check_dockerfile(rep: Report) -> None:
    df = BASE_DIR / "Dockerfile"
    if not df.exists():
        rep.fail("Dockerfile 存在", "找不到 Dockerfile")
        return
    text = df.read_text(encoding="utf-8")

    # 1) 每个本地应用都必须被 COPY 进镜像
    copied = set(re.findall(r"^COPY\s+(?:--chown=\S+\s+)?(\S+)\s+", text, re.M))
    apps = _installed_app_dirs()
    if not apps:
        # 解析不到应用列表时绝不能当成"全部通过"—— 那样检查等于没做
        rep.fail("Dockerfile 复制了全部本地应用",
                 "未能从 base.py 解析出本地应用列表（检查本身失效，不是真的通过）")
    else:
        missing = [a for a in apps if a not in copied]
        if missing:
            rep.fail("Dockerfile 复制了全部本地应用",
                     "漏掉：%s —— 容器启动会 ImportError（本地 runserver 测不出来）"
                     % ", ".join(missing))
        else:
            rep.ok("Dockerfile 复制了全部本地应用",
                   "%d 个：%s" % (len(apps), ", ".join(apps)))

    # 2) 运行期依赖（Pillow 需要 zlib；psycopg 需要 libpq）
    for pkg, why in (("zlib1g", "Pillow 的 libz.so.1"), ("libpq5", "psycopg")):
        if pkg not in text:
            rep.fail("Dockerfile 运行期依赖 %s" % pkg, "缺少 %s（%s 需要）" % (pkg, why))
    if "zlib1g" in text and "libpq5" in text:
        rep.ok("Dockerfile 运行期依赖完整", "zlib1g + libpq5")

    # 3) 非 root 运行
    if re.search(r"^USER\s+(?!root)", text, re.M):
        rep.ok("容器以非 root 运行")
    else:
        rep.fail("容器以非 root 运行", "Dockerfile 未切换到非 root 用户")

    # 4) 健康检查指向 ready 探针
    if "/health/ready" in text:
        rep.ok("镜像内置健康检查", "指向 /health/ready")
    else:
        rep.fail("镜像内置健康检查", "HEALTHCHECK 未指向 /health/ready")

    # 5) 启动时先迁移再起服务
    if "migrate --noinput" in text and "gunicorn" in text:
        rep.ok("启动顺序正确", "先 migrate 再 gunicorn")
    else:
        rep.fail("启动顺序正确", "启动命令应包含 migrate 与 gunicorn")


# ---------------------------------------------------------------------------
# docker-compose
# ---------------------------------------------------------------------------

def _compose_text() -> str:
    p = BASE_DIR / "docker-compose.yml"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _service_block(text: str, name: str) -> str:
    """粗略切出某个服务的配置块（到下一个同级服务或文件末尾）。"""
    m = re.search(r"^  %s:\s*$" % re.escape(name), text, re.M)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"^  [a-zA-Z_][\w-]*:\s*$", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def check_compose(rep: Report) -> None:
    text = _compose_text()
    if not text:
        rep.fail("docker-compose.yml 存在", "找不到文件")
        return

    try:
        import yaml  # noqa: F401
        shell_only = False
    except ImportError:
        shell_only = False

    app = _service_block(text, "app")
    db = _service_block(text, "db")
    nginx = _service_block(text, "nginx")

    # 1) 附件目录必须 bind mount 到宿主机持久路径
    if re.search(r"^\s*-\s*\S*private_media:/data/private_media\s*$", app, re.M):
        rep.ok("附件目录挂载到宿主机持久路径", "/data/private_media")
    else:
        rep.fail("附件目录挂载到宿主机持久路径",
                 "app 未把 /data/private_media 绑定到宿主机目录 —— "
                 "附件会写进容器临时层，重建即丢")

    # 2) 容器内 PRIVATE_MEDIA_ROOT 必须与挂载点一致
    if re.search(r'^\s*PRIVATE_MEDIA_ROOT:\s*"/data/private_media"\s*$', app, re.M):
        rep.ok("PRIVATE_MEDIA_ROOT 与挂载点一致")
    else:
        rep.fail("PRIVATE_MEDIA_ROOT 与挂载点一致",
                 "容器内路径与 bind mount 目标不一致，应用会写到未持久化的目录")

    # 3) 存储后端默认 local（P5 规则）
    m = re.search(r"PRIVATE_STORAGE_BACKEND:\s*\S*?-(\w+)\}", app)
    default_backend = m.group(1) if m else None
    if default_backend == "local":
        rep.ok("存储后端默认 local", "未配置时用服务器本地磁盘")
    elif default_backend == "cos":
        rep.fail("存储后端默认 local", "compose 仍默认 cos（P5 已改为本地磁盘）")
    else:
        rep.fail("存储后端默认 local", "未找到 PRIVATE_STORAGE_BACKEND 默认值")

    # 4) 数据库数据也落在宿主机（与附件同根，便于备份）
    if re.search(r"^\s*-\s*\S*postgres:/var/lib/postgresql/data\s*$", db, re.M):
        rep.ok("数据库数据落在宿主机目录", "与附件同根，备份只需处理一个目录")
    else:
        rep.fail("数据库数据落在宿主机目录",
                 "db 仍用具名卷 —— 备份/迁移需绕 docker volume 命令，易漏")

    # 5) 数据库不对外发布端口
    if re.search(r"^\s*ports:", db, re.M):
        rep.fail("数据库不对外暴露", "db 服务声明了 ports，5432 可能暴露到宿主机")
    else:
        rep.ok("数据库不对外暴露", "db 未声明 ports")

    # 6) nginx 不得挂载私有附件目录
    if "private_media" in nginx:
        rep.fail("nginx 不挂私有附件", "nginx 挂了附件目录 —— 会绕过 Django 鉴权直接可下载")
    else:
        rep.ok("nginx 不挂私有附件", "私有附件只经 Django 下发")

    # 7) 数据库在内部网络
    if "internal: true" in text:
        rep.ok("数据库处于内部网络", "backend 网络 internal: true")
    else:
        rep.fail("数据库处于内部网络", "未找到 internal: true 网络")

    # 8) 敏感值不写死在 compose 里
    if re.search(r"SECRET_KEY:\s*[\"']?[A-Za-z0-9_\-]{20,}", text):
        rep.fail("compose 不含真实密钥", "compose 里疑似写死了 SECRET_KEY")
    else:
        rep.ok("compose 不含真实密钥", "敏感值均来自 .env")


# ---------------------------------------------------------------------------
# 安全：数据与密钥不入库
# ---------------------------------------------------------------------------

def check_repo_hygiene(rep: Report) -> None:
    gi = BASE_DIR / ".gitignore"
    if not gi.exists():
        rep.fail(".gitignore 存在", "缺少 .gitignore")
        return
    gtext = gi.read_text(encoding="utf-8")

    must_ignore = {
        ".env": "环境变量文件（含密钥）",
        "private_media/": "私有附件目录",
        "logs/": "日志目录",
    }
    missing = [f"{p}（{d}）" for p, d in must_ignore.items() if p not in gtext]
    if missing:
        rep.fail(".gitignore 覆盖敏感内容", "未忽略：%s" % "、".join(missing))
    else:
        rep.ok(".gitignore 覆盖敏感内容", ".env / private_media / logs")

    # 数据目录（数据库 + 附件）也不能进仓库
    if "data/" in gtext:
        rep.ok(".gitignore 忽略默认数据目录", "data/")
    else:
        rep.fail(".gitignore 忽略默认数据目录",
                 "compose 默认 PINGPONG_DATA_DIR=./data，若未忽略会误提交数据库文件")

    # 真的没被跟踪吗（以 git 为准，而不是只看 gitignore）
    try:
        import subprocess
        out = subprocess.run(
            ["git", "ls-files"], cwd=str(BASE_DIR),
            capture_output=True, encoding="utf-8", errors="replace", timeout=60,
        ).stdout
    except Exception as exc:  # noqa: BLE001
        rep.skip("敏感文件未被 Git 跟踪", "无法执行 git：%r" % exc)
        return

    tracked = out.splitlines()
    bad = []
    for f in tracked:
        low = f.replace("\\", "/")
        if low == ".env" or low.startswith("private_media/") \
                or low.startswith("logs/") or low.startswith("data/") \
                or low.endswith(".sqlite3"):
            bad.append(f)
    if bad:
        rep.fail("敏感文件未被 Git 跟踪",
                 "已被跟踪：%s —— 需 git rm --cached 并清理历史" % bad[:5])
    else:
        rep.ok("敏感文件未被 Git 跟踪", "共 %d 个受跟踪文件，无敏感项" % len(tracked))

    # 受跟踪文件里不应出现真实凭据形态
    try:
        import subprocess
        out = subprocess.run(
            ["git", "grep", "-nEI",
             r"(SECRET_KEY\s*=\s*['\"][^'\"\s]{20,}|AKID[A-Za-z0-9]{10,})",
             "--", "*.py", "*.yml", "*.yaml", "*.conf", "*.md"],
            cwd=str(BASE_DIR), capture_output=True, encoding="utf-8",
            errors="replace", timeout=60,
        ).stdout
        hits = [l for l in out.splitlines()
                if "example" not in l.lower() and "你的" not in l and "xxx" not in l.lower()]
    except Exception as exc:  # noqa: BLE001
        rep.skip("库内无真实凭据", "无法执行 git grep：%r" % exc)
        return
    if hits:
        rep.fail("库内无真实凭据", "疑似硬编码凭据：%s" % hits[:3])
    else:
        rep.ok("库内无真实凭据", "未发现硬编码 SECRET_KEY / COS 密钥")


# ---------------------------------------------------------------------------
# 配置：生产 fail-closed
# ---------------------------------------------------------------------------

def check_prod_settings(rep: Report) -> None:
    prod = BASE_DIR / "config" / "settings" / "prod.py"
    if not prod.exists():
        rep.fail("生产配置存在", "找不到 prod.py")
        return
    text = prod.read_text(encoding="utf-8")

    # 不能再出现硬编码 cos
    if re.search(r'^PRIVATE_STORAGE_BACKEND\s*=\s*["\']cos["\']', text, re.M):
        rep.fail("生产存储后端不再硬编码 cos",
                 "prod.py 仍写死 ='cos'，P5 已改为默认本地磁盘")
    else:
        rep.ok("生产存储后端不再硬编码 cos", "改为从环境变量读取")

    # 默认值应为 local（未配置时）
    if re.search(r'env\(\s*["\']PRIVATE_STORAGE_BACKEND["\']', text):
        rep.ok("生产存储后端来自环境变量")
    else:
        rep.fail("生产存储后端来自环境变量", "未通过 env() 读取")

    # 必须以 env 方式读取数据目录
    if "PRIVATE_MEDIA_ROOT" in text:
        rep.ok("生产配置处理了 PRIVATE_MEDIA_ROOT")
    else:
        rep.fail("生产配置处理了 PRIVATE_MEDIA_ROOT",
                 "未处理数据目录，无法区分显式配置与开发默认值")

    # DEBUG 必须强制 False
    if re.search(r"^DEBUG\s*=\s*False", text, re.M):
        rep.ok("生产 DEBUG 强制关闭")
    else:
        rep.fail("生产 DEBUG 强制关闭", "prod.py 未强制 DEBUG=False")


# ---------------------------------------------------------------------------
# 文档
# ---------------------------------------------------------------------------

def check_docs(rep: Report) -> None:
    doc = BASE_DIR / "deploy" / "DEPLOY.md"
    if not doc.exists():
        rep.fail("部署文档存在", "缺少 deploy/DEPLOY.md")
        return
    text = doc.read_text(encoding="utf-8")
    rep.ok("部署文档存在", "%d 行" % len(text.splitlines()))

    # 文档必须覆盖关键章节（少一节，照着做就会卡住）
    required = {
        "服务器初始化": ["apt update", "docker"],
        "数据目录与权限": ["chown", "1001"],
        "生产配置": ["DJANGO_SECRET_KEY", "PRIVATE_MEDIA_ROOT", "PINGPONG_DATA_DIR"],
        "HTTPS": ["certbot"],
        "健康检查": ["/health/ready"],
        "备份": ["pg_dump", "pg_restore"],
        "故障排查": ["StorageConfigError", "502"],
        "上线清单": ["检查清单"],
    }
    missing = []
    for section, keys in required.items():
        if not all(k in text for k in keys):
            missing.append(section)
    if missing:
        rep.fail("部署文档覆盖完整", "缺少或漏写：%s" % "、".join(missing))
    else:
        rep.ok("部署文档覆盖完整", "%d 个关键章节" % len(required))

    # 文档里的占位域名必须明确提示要替换
    if "stat.example.com" in text or "你的域名" in text:
        rep.ok("文档提示替换占位域名")
    else:
        rep.fail("文档提示替换占位域名", "未提示替换 nginx 占位域名")

    # .env.example 必须说明本地磁盘方案
    env_ex = BASE_DIR / ".env.example"
    if env_ex.exists():
        et = env_ex.read_text(encoding="utf-8")
        if "PRIVATE_MEDIA_ROOT" in et and "PINGPONG_DATA_DIR" in et:
            rep.ok(".env.example 与部署方案一致", "含本地磁盘与数据目录变量")
        else:
            rep.fail(".env.example 与部署方案一致",
                     "缺少 PRIVATE_MEDIA_ROOT 或 PINGPONG_DATA_DIR")


# ---------------------------------------------------------------------------
# 环境可达性（跳过项，不代表通过）
# ---------------------------------------------------------------------------

def check_environment(rep: Report) -> None:
    import shutil
    if shutil.which("docker"):
        try:
            import subprocess
            r = subprocess.run(["docker", "compose", "version"],
                               capture_output=True, encoding="utf-8", timeout=20)
            if r.returncode == 0:
                rep.ok("Docker 可用", r.stdout.strip().splitlines()[0])
            else:
                rep.skip("Docker 实际可构建", "docker 存在但 compose 不可用")
        except Exception as exc:  # noqa: BLE001
            rep.skip("Docker 实际可构建", "%r" % exc)
    else:
        rep.skip("Docker 实际可构建",
                 "本机未安装 docker —— 镜像构建与容器启动未在本机验证")


def main() -> int:
    verbose = "-v" in sys.argv or "--verbose" in sys.argv
    rep = Report(verbose=verbose)
    print("部署配置自检 —— 项目：%s" % BASE_DIR)
    print()

    check_dockerfile(rep)
    check_compose(rep)
    check_repo_hygiene(rep)
    check_prod_settings(rep)
    check_docs(rep)
    check_environment(rep)

    return rep.render()


if __name__ == "__main__":
    sys.exit(main())
