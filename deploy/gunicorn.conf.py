"""
Gunicorn 生产配置。

用途：docker-compose 与正式部署均通过本文件启动应用服务。
注意：Gunicorn 依赖 fcntl，**不能在 Windows 上运行**；
      本机开发请用 `python manage.py runserver`，生产用 Docker/Linux。
"""
import os

# ---------------------------------------------------------------------------
# 监听
# ---------------------------------------------------------------------------
# 仅监听容器内部网络；对外由 Nginx 反代，应用端口不直接暴露给公网
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")
backlog = 2048

# ---------------------------------------------------------------------------
# 进程模型
# ---------------------------------------------------------------------------
# 单机小流量默认 2 个 worker。此前的 CPU*2+1 在 4 GiB 机器上会启动 5 个，
# 对 PostgreSQL + 图片处理来说没有必要。可按实际负载通过环境变量调高。
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
worker_class = "sync"
threads = int(os.environ.get("GUNICORN_THREADS", "2"))
worker_tmp_dir = "/dev/shm"          # 避免磁盘 I/O 拖慢心跳
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "60"))
graceful_timeout = 30
keepalive = 5

# ---------------------------------------------------------------------------
# 安全与健壮性
# ---------------------------------------------------------------------------
# 单个请求最大处理数，规避潜在内存泄漏累积
max_requests = 1000
max_requests_jitter = 100

# 由 Nginx 反代时读取转发头（用于识别真实客户端协议/IP）
forwarded_allow_ips = os.environ.get("GUNICORN_FORWARDED_ALLOW_IPS", "127.0.0.1")

# 生产不开启 reload / 不写访问日志到 stdout（由 Nginx 记录访问日志）
reload = False
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")

# 进程名便于排查
proc_name = "pingpong_stats"

# ---------------------------------------------------------------------------
# 启动钩子
# ---------------------------------------------------------------------------
def on_starting(server):
    server.log.info("Gunicorn 正在启动：%s workers=%d bind=%s", proc_name, workers, bind)


def worker_int(worker):
    worker.log.info("worker %s 收到中断信号", worker.pid)
