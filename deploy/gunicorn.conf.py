"""
Gunicorn 生产配置。

用途：docker-compose 与正式部署均通过本文件启动应用服务。
注意：Gunicorn 依赖 fcntl，**不能在 Windows 上运行**；
      本机开发请用 `python manage.py runserver`，生产用 Docker/Linux。
"""
import multiprocessing
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
# 默认按 CPU 核数 * 2 + 1；容器里可通过环境变量固定，便于容量规划
workers = int(os.environ.get("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
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
