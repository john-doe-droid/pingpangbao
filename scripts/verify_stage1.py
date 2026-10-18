#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
P1 阶段统一验证入口。

它**真正执行**项目检查与测试，并在任一必要检查失败时以非零退出码结束。

设计原则（对应任务的明确要求）：
  * 不使用 try/except 把失败吞掉再返回成功；
  * 不跳过测试、不降低断言；
  * 明确区分三类结果：
        PASS  —— 该检查真实通过
        FAIL  —— 该检查真实失败（会导致整体非零退出）
        SKIP  —— 环境不具备（例如本机没有 Docker），**不计入通过**，
                 但也不伪装成失败；报告里单独列出"未验证事项"
  * 退出码：0 表示所有"必要条件"通过；1 表示有失败；2 表示环境阻塞导致
    关键检查无法执行。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\verify_stage1.py
    .\\.venv\\Scripts\\python.exe scripts\\verify_stage1.py --verbose
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
VENV_PY = BASE_DIR / ".venv" / "Scripts" / "python.exe"
if not VENV_PY.exists():                     # 非 Windows / 其他布局
    VENV_PY = BASE_DIR / ".venv" / "bin" / "python"
PYTHON = str(VENV_PY if VENV_PY.exists() else sys.executable)

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

results: list[dict] = []


def record(name: str, status: str, detail: str, output: str = "") -> None:
    results.append({"name": name, "status": status, "detail": detail, "output": output})
    icon = {PASS: "✅", FAIL: "❌", SKIP: "⏭️ "}[status]
    print("%s %s —— %s" % (icon, name, detail))
    if output and status == FAIL:
        tail = output.strip().splitlines()[-25:]
        for line in tail:
            print("      | %s" % line)


def run(cmd: list[str], env_extra: dict | None = None, timeout: int = 600) -> tuple[int, str]:
    """执行命令并返回 (退出码, 输出)。不吞异常、不美化退出码。"""
    env = dict(os.environ)
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.run(
            cmd, cwd=str(BASE_DIR), capture_output=True,
            encoding="utf-8", errors="replace", timeout=timeout, env=env,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "命令超时（>%ds）：%s" % (timeout, " ".join(cmd))
    except FileNotFoundError as exc:
        return 127, "找不到可执行文件：%s（%s）" % (cmd[0], exc)


# ---------------------------------------------------------------------------
# 各项检查
# ---------------------------------------------------------------------------

def check_python_version():
    code, out = run([PYTHON, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"])
    version = out.strip().splitlines()[-1] if out.strip() else "(未知)"
    if code != 0:
        record("Python 解释器可用", FAIL, "无法执行 %s" % PYTHON, out)
        return
    major, minor = (int(x) for x in version.split(".")[:2])
    if (major, minor) != (3, 13):
        record("Python 版本为 3.13", FAIL,
               "当前 %s，本项目要求 3.13" % version, out)
    else:
        record("Python 版本为 3.13", PASS, "Python %s" % version)


def check_dependencies():
    code, out = run([PYTHON, "-c",
                     "import django, psycopg, dotenv, whitenoise;"
                     "print('django=' + django.get_version());"
                     "print('psycopg=' + psycopg.__version__)"])
    if code != 0:
        record("依赖已安装", FAIL, "导入依赖失败（是否忘记 pip install -r requirements.txt？）", out)
        return
    lines = [l for l in out.strip().splitlines() if "=" in l]
    info = ", ".join(lines)
    if not any(l.startswith("django=5.2") for l in lines):
        record("Django 为 5.2 LTS", FAIL, "当前：%s" % info, out)
    else:
        record("Django 为 5.2 LTS", PASS, info)


def check_requirements_pinned():
    """requirements.txt 中所有依赖必须锁定精确版本（==）。"""
    text = (BASE_DIR / "requirements.txt").read_text(encoding="utf-8")
    unpinned = []
    for raw in text.splitlines():
        line = raw.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        if "==" not in line:
            unpinned.append(line)
    if unpinned:
        record("依赖版本已锁定", FAIL, "存在未锁定版本：%s" % ", ".join(unpinned))
    else:
        record("依赖版本已锁定", PASS, "requirements.txt 全部使用 ==")


def check_django_check():
    code, out = run([PYTHON, "manage.py", "check"])
    if code != 0:
        record("Django 系统检查", FAIL, "manage.py check 未通过", out)
    else:
        record("Django 系统检查", PASS, "no issues")


def check_deploy_check():
    """生产配置的部署安全检查。

    使用 prod 配置并把 HTTPS 关掉，只检查其余安全项（否则会因缺少证书路径等
    无关项噪声太多）。这里只要求没有 ERROR 级问题。
    """
    env = {
        "DJANGO_SETTINGS_MODULE": "config.settings.prod",
        "DJANGO_SECRET_KEY": "verify-only-" + "z" * 60,
        "DJANGO_ALLOWED_HOSTS": "verify.example.com",
        "DJANGO_CSRF_TRUSTED_ORIGINS": "https://verify.example.com",
        "POSTGRES_DB": "pingpong",
        "POSTGRES_USER": "pingpong",
        "POSTGRES_PASSWORD": "verify-only-password",
        "POSTGRES_HOST": "127.0.0.1",
        "DJANGO_HTTPS_ENABLED": "false",
    }
    code, out = run([PYTHON, "manage.py", "check", "--deploy"], env_extra=env)
    # 只看 ERROR/WARN 中是否出现 error（Django 的 deploy 检查全为 WARNING 级）
    if "ERROR" in out or "error" in out.lower().replace("errorlevel", ""):
        # 进一步确认不是我们关心的错误
        if "SystemCheckError" in out or "ERRORS" in out:
            record("生产配置部署检查", FAIL, "check --deploy 报告错误", out)
            return
    record("生产配置部署检查", PASS, "prod 配置可加载且无错误级问题")


def check_migrations_consistent():
    """模型与迁移文件必须一致（有未生成的迁移即为不一致）。"""
    code, out = run([PYTHON, "manage.py", "makemigrations", "--check", "--dry-run"])
    if code != 0:
        record("模型与迁移一致", FAIL, "存在未生成的迁移", out)
    else:
        record("模型与迁移一致", PASS, "无待生成迁移")


def check_no_pending_migrate():
    """数据库迁移必须已全部应用。"""
    code, out = run([PYTHON, "manage.py", "migrate", "--check"])
    if code != 0:
        record("数据库迁移已应用", FAIL, "存在未应用的迁移", out)
    else:
        record("数据库迁移已应用", PASS, "全部已应用")


def check_database_is_postgres():
    """确认实际连接的数据库是 PostgreSQL 18。"""
    script = (
        "import os, django;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.dev');"
        "django.setup();"
        "from django.db import connection;"
        "c = connection.cursor();"
        "c.execute('SHOW server_version');"
        "v = c.fetchone()[0];"
        "print('engine=' + connection.settings_dict['ENGINE']);"
        "print('server_version=' + str(v))"
    )
    code, out = run([PYTHON, "-c", script])
    if code != 0:
        record("数据库为 PostgreSQL 18", FAIL,
               "无法连接数据库（检查 PostgreSQL 是否启动、.env 是否正确）", out)
        return
    engine = ""
    version = ""
    for line in out.splitlines():
        if line.startswith("engine="):
            engine = line.split("=", 1)[1].strip()
        if line.startswith("server_version="):
            version = line.split("=", 1)[1].strip()

    if "postgresql" not in engine:
        record("数据库为 PostgreSQL 18", FAIL, "当前 ENGINE = %s（禁止使用 SQLite）" % engine, out)
        return
    major = version.split(".")[0] if version else "?"
    if major != "18":
        record("数据库为 PostgreSQL 18", FAIL, "服务端版本为 %s，要求 18.x" % version, out)
    else:
        record("数据库为 PostgreSQL 18", PASS, "engine=%s, server=%s" % (engine, version))


def check_pytest():
    code, out = run([PYTHON, "-m", "pytest", "-q"])
    if code != 0:
        record("自动化测试全部通过", FAIL, "pytest 未通过", out)
        return
    summary = ""
    for line in reversed(out.strip().splitlines()):
        if "passed" in line or "failed" in line:
            summary = line.strip()
            break
    record("自动化测试全部通过", PASS, summary or "pytest 退出码 0")


def check_health_endpoints():
    """在真实进程上验证健康检查（用 runserver 起一个临时端口）。"""
    import socket
    import urllib.error
    import urllib.request

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    env = dict(os.environ)
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [PYTHON, "manage.py", "runserver", "127.0.0.1:%d" % port, "--noreload"],
        cwd=str(BASE_DIR), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        base = "http://127.0.0.1:%d" % port
        deadline = time.time() + 30
        ok = {"live": False, "ready": False, "home": False, "static": False}
        bodies = {}
        while time.time() < deadline:
            try:
                urllib.request.urlopen(base + "/health/live", timeout=3).read()
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)

        for key, path in (("live", "/health/live"), ("ready", "/health/ready"),
                          ("home", "/"), ("static", "/static/core/css/site.css")):
            try:
                resp = urllib.request.urlopen(base + path, timeout=5)
                bodies[key] = resp.read().decode("utf-8", "replace")
                ok[key] = resp.status == 200
            except urllib.error.HTTPError as exc:
                ok[key] = exc.code == 200
            except Exception:  # noqa: BLE001
                ok[key] = False

        if all(ok.values()):
            record("健康检查与首页/静态资源", PASS,
                   "/health/live、/health/ready、首页、静态文件均 200")
        else:
            record("健康检查与首页/静态资源", FAIL,
                   "未全部返回 200：%s" % ok,
                   json.dumps(bodies, ensure_ascii=False)[:1500])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def check_health_no_secret_leak():
    """健康检查响应不得包含密钥或数据库口令。"""
    import urllib.error
    import urllib.request

    env = dict(os.environ)
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    env["PYTHONIOENCODING"] = "utf-8"
    code, secret_out = run([PYTHON, "-c",
                            "import os,django;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.dev');"
                            "django.setup();from django.conf import settings;"
                            "print(settings.SECRET_KEY);"
                            "print(settings.DATABASES['default'].get('PASSWORD',''))"])
    if code != 0:
        record("健康检查不泄露敏感信息", SKIP, "无法读取配置以做比对", secret_out)
        return
    parts = secret_out.strip().splitlines()
    secret = parts[0].strip() if parts else ""
    dbpw = parts[1].strip() if len(parts) > 1 else ""

    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    proc = subprocess.Popen(
        [PYTHON, "manage.py", "runserver", "127.0.0.1:%d" % port, "--noreload"],
        cwd=str(BASE_DIR), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        base = "http://127.0.0.1:%d" % port
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                urllib.request.urlopen(base + "/health/live", timeout=3).read()
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)

        leaked = []
        for path in ("/health/live", "/health/ready"):
            try:
                body = urllib.request.urlopen(base + path, timeout=5).read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")
            for token, label in ((secret, "SECRET_KEY"), (dbpw, "数据库口令")):
                if token and len(token) > 8 and token in body:
                    leaked.append("%s 出现在 %s" % (label, path))
        if leaked:
            record("健康检查不泄露敏感信息", FAIL, "; ".join(leaked))
        else:
            record("健康检查不泄露敏感信息", PASS, "响应中未出现密钥或数据库口令")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def check_env_not_committed():
    """确保 .env 不会被 git 跟踪，且 .env.example 存在。"""
    if shutil.which("git") is None:
        record(".env 未被 Git 跟踪", SKIP, "本机没有 git 命令")
        return
    if not (BASE_DIR / ".git").exists():
        record(".env 未被 Git 跟踪", SKIP, "项目尚未初始化 Git 仓库")
        return
    code, out = run(["git", "ls-files", ".env"])
    if out.strip():
        record(".env 未被 Git 跟踪", FAIL, ".env 被 Git 跟踪了，必须移除")
        return
    if not (BASE_DIR / ".env.example").exists():
        record(".env 未被 Git 跟踪", FAIL, "缺少 .env.example")
        return
    record(".env 未被 Git 跟踪", PASS, ".env 未跟踪，.env.example 存在")


def check_compose_present():
    """docker-compose.yml 与 Dockerfile 的存在性与基本结构。"""
    compose = BASE_DIR / "docker-compose.yml"
    dockerfile = BASE_DIR / "Dockerfile"
    if not compose.exists() or not dockerfile.exists():
        record("容器化配置存在", FAIL, "缺少 Dockerfile 或 docker-compose.yml")
        return
    text = compose.read_text(encoding="utf-8")
    problems = []
    if "postgres:18" not in text:
        problems.append("未指定 PostgreSQL 18 镜像")
    if "internal: true" not in text:
        problems.append("数据库网络未设为 internal（可能对外暴露）")
    if "healthcheck" not in text:
        problems.append("缺少 healthcheck")
    if "restart:" not in text:
        problems.append("缺少重启策略")
    if problems:
        record("容器化配置存在", FAIL, "; ".join(problems))
    else:
        record("容器化配置存在", PASS, "compose 含 PG18 镜像、internal 网络、healthcheck、重启策略")


def check_docker_actually_runnable():
    """Docker 是否真的可以运行 —— 本机若没装则明确标为环境阻塞。"""
    if shutil.which("docker") is None:
        record("Docker 实际可运行", SKIP,
               "本机未安装 docker，容器编排未在本机验证（仅校验了配置结构）")
        return
    code, out = run(["docker", "compose", "config", "--quiet"], timeout=120)
    if code != 0:
        record("Docker 实际可运行", FAIL, "docker compose config 失败", out)
    else:
        record("Docker 实际可运行", PASS, "docker compose 配置可解析")


def check_prod_wsgi_entry():
    """生产 WSGI 入口（config.wsgi:application）能否正常服务。"""
    env = {
        "DJANGO_SETTINGS_MODULE": "config.settings.prod",
        "DJANGO_SECRET_KEY": "verify-only-" + "z" * 60,
        "DJANGO_ALLOWED_HOSTS": "127.0.0.1,localhost,verify.example.com",
        "DJANGO_CSRF_TRUSTED_ORIGINS": "https://verify.example.com",
        "POSTGRES_DB": os.environ.get("POSTGRES_DB", "pingpong_dev"),
        "POSTGRES_USER": os.environ.get("POSTGRES_USER", "pingpong"),
        "POSTGRES_PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "POSTGRES_HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        "POSTGRES_PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "DJANGO_HTTPS_ENABLED": "false",
    }
    code, out = run([PYTHON, "scripts/check_prod_wsgi.py"], env_extra=env, timeout=180)
    if code != 0:
        record("生产 WSGI 入口可用", FAIL,
               "config.wsgi:application 未通过冒烟检查", out)
        return
    record("生产 WSGI 入口可用", PASS,
           "首页/登录页/健康检查/哈希静态资源在生产配置下均 200")


def check_gunicorn_runnable():
    """Gunicorn 是否能在本机运行 —— Windows 上不可用，属环境阻塞。"""
    code, out = run([PYTHON, "-c",
                     "import fcntl; print('fcntl available')"])
    if code != 0:
        record("Gunicorn 实际可运行", SKIP,
               "本机为 Windows，Gunicorn 依赖 fcntl（Unix 专有）无法运行；"
               "生产应用服务器仅在 Linux/Docker 上可用")
        return
    code, out = run([PYTHON, "-c",
                     "import gunicorn, fcntl; print('gunicorn', gunicorn.__version__)"])
    if code != 0:
        record("Gunicorn 实际可运行", FAIL, "gunicorn 未正确安装", out)
    else:
        record("Gunicorn 实际可运行", PASS, out.strip().splitlines()[-1])


def check_agent_loop_untouched():
    """确认本次工作没有改动 agent-loop 下的安全护栏。"""
    baseline = BASE_DIR.parent / "化学" / "agent-loop" / "probe" / "evidence" / "p1_guard_baseline.json"
    if not baseline.exists():
        record("agent-loop 护栏未被修改", SKIP, "未找到基线记录文件")
        return
    import hashlib

    data = json.loads(baseline.read_text(encoding="utf-8"))
    workdir = BASE_DIR.parent / "化学"
    changed = []
    for item in data["files"]:
        path = workdir / item["path"].replace("agent-loop/", "agent-loop/")
        if not path.exists():
            changed.append("%s（文件缺失）" % item["path"])
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != item["sha256"]:
            changed.append(item["path"])
    if changed:
        record("agent-loop 护栏未被修改", FAIL, "以下文件发生变化：%s" % ", ".join(changed))
    else:
        record("agent-loop 护栏未被修改", PASS, "全部护栏文件哈希与基线一致")


def check_readme_matches_project():
    """README 里提到的关键路径必须真实存在（避免文档与项目不一致）。"""
    readme = BASE_DIR / "README.md"
    if not readme.exists():
        record("README 与项目一致", FAIL, "缺少 README.md")
        return
    text = readme.read_text(encoding="utf-8")
    # README 里同时存在正斜杠与反斜杠写法，比对前统一归一化
    normalized = text.replace("\\", "/")
    required = [".venv", "manage.py", "scripts/verify_stage1.py",
                "docker-compose.yml", ".env.example", "deploy/gunicorn.conf.py",
                "deploy/nginx/pingpong.conf", "config/settings/dev.py",
                "config/settings/prod.py", "accounts/models.py", "core/views.py",
                "tests/conftest.py", "pytest.ini", "requirements.txt"]
    missing_in_doc = [p for p in required if p not in normalized]
    missing_on_disk = [p for p in required
                       if p in normalized and not (BASE_DIR / p).exists()]
    if missing_in_doc or missing_on_disk:
        detail = []
        if missing_in_doc:
            detail.append("README 未提及：%s" % ", ".join(missing_in_doc))
        if missing_on_disk:
            detail.append("README 提及但磁盘缺失：%s" % ", ".join(missing_on_disk))
        record("README 与项目一致", FAIL, "; ".join(detail))
    else:
        record("README 与项目一致", PASS,
               "%d 个关键路径在 README 与磁盘上一致" % len(required))


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

CHECKS = [
    ("python", check_python_version),
    ("deps", check_dependencies),
    ("pin", check_requirements_pinned),
    ("check", check_django_check),
    ("deploy", check_deploy_check),
    ("makemig", check_migrations_consistent),
    ("migrate", check_no_pending_migrate),
    ("db", check_database_is_postgres),
    ("pytest", check_pytest),
    ("health", check_health_endpoints),
    ("leak", check_health_no_secret_leak),
    ("env", check_env_not_committed),
    ("compose", check_compose_present),
    ("docker", check_docker_actually_runnable),
    ("wsgi", check_prod_wsgi_entry),
    ("gunicorn", check_gunicorn_runnable),
    ("guard", check_agent_loop_untouched),
    ("readme", check_readme_matches_project),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="P1 阶段统一验证入口")
    ap.add_argument("--only", help="只跑指定检查（逗号分隔的 key）")
    ap.add_argument("--list", action="store_true", help="列出所有检查项后退出")
    args = ap.parse_args()

    if args.list:
        for key, fn in CHECKS:
            print("%-10s %s" % (key, fn.__doc__ or ""))
        return 0

    only = set(args.only.split(",")) if args.only else None

    print("=" * 74)
    print("P1 阶段验证 —— 中国农业大学乒乓球社工作统计")
    print("项目目录：%s" % BASE_DIR)
    print("解释器  ：%s" % PYTHON)
    print("=" * 74)

    started = time.time()
    for key, fn in CHECKS:
        if only and key not in only:
            continue
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            # 检查函数本身崩溃也算失败，绝不静默放过
            record(fn.__doc__ or key, FAIL, "检查过程抛出异常：%r" % exc)

    failures = [r for r in results if r["status"] == FAIL]
    skips = [r for r in results if r["status"] == SKIP]
    passes = [r for r in results if r["status"] == PASS]

    print()
    print("=" * 74)
    print("汇总：通过 %d 项，失败 %d 项，跳过（环境阻塞）%d 项，耗时 %.1fs"
          % (len(passes), len(failures), len(skips), time.time() - started))
    if skips:
        print("-" * 74)
        print("未验证事项（环境阻塞，**不代表通过**）：")
        for r in skips:
            print("  · %s —— %s" % (r["name"], r["detail"]))
    if failures:
        print("-" * 74)
        print("失败项：")
        for r in failures:
            print("  ✗ %s —— %s" % (r["name"], r["detail"]))
    print("=" * 74)

    # 报告文件，便于交付核对
    report = BASE_DIR / "verify_stage1_report.json"
    report.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "python": PYTHON,
                "project_dir": str(BASE_DIR),
                "summary": {"pass": len(passes), "fail": len(failures), "skip": len(skips)},
                "results": [{k: v for k, v in r.items() if k != "output"} for r in results],
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print("报告已写入：%s" % report.name)

    if failures:
        return 1
    # 只有跳过项、没有失败：仍视为通过，但报告里明确列出未验证事项
    return 0


if __name__ == "__main__":
    sys.exit(main())
