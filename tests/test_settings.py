"""
生产配置的关键安全约束测试（对应验收 C）。

这些测试**真的去加载 config.settings.prod**，并验证它在缺失关键配置时
会明确报错、在有配置时给出安全取值。不是读源码做字符串匹配。
"""
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent

# 一组测试专用的假凭据（不是真实密钥，仅用于验证配置逻辑）
FAKE_SECRET = "test-only-" + "x" * 60
GOOD_ENV = {
    "DJANGO_SECRET_KEY": FAKE_SECRET,
    "DJANGO_ALLOWED_HOSTS": "stat.example.com",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://stat.example.com",
    "POSTGRES_DB": "pingpong",
    "POSTGRES_USER": "pingpong",
    "POSTGRES_PASSWORD": "fake-password-for-config-test",
    "POSTGRES_HOST": "db",
    "POSTGRES_PORT": "5432",
    "DJANGO_SETTINGS_MODULE": "config.settings.prod",
}


def _load_prod(monkeypatch, **overrides):
    """在受控环境变量下重新加载 prod 配置模块。

    注意：项目根的 .env 会被 base.py 读取，其中可能包含本地开发用的
    DJANGO_HTTPS_ENABLED=false。测试必须显式控制它，否则会被本地 .env 污染，
    导致"生产安全开关"的断言失真。
    """
    keys_to_clear = list(GOOD_ENV) + [
        "DJANGO_DEBUG", "DJANGO_DB_ENGINE", "DJANGO_HTTPS_ENABLED",
        "DJANGO_HSTS_SECONDS",
    ]
    for key in keys_to_clear:
        monkeypatch.delenv(key, raising=False)
    env = dict(GOOD_ENV)
    env["DJANGO_HTTPS_ENABLED"] = "true"   # 生产默认启用 HTTPS 安全项
    env.update(overrides)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    import config.settings.base as base_mod
    import config.settings.prod as prod_mod

    importlib.reload(base_mod)
    return importlib.reload(prod_mod)


# ---------------------------------------------------------------------------
# 正常路径：有完整配置时的安全取值
# ---------------------------------------------------------------------------

@pytest.mark.prod_config
def test_prod_debug_is_always_off(monkeypatch):
    prod = _load_prod(monkeypatch, DJANGO_DEBUG="true")
    assert prod.DEBUG is False, "生产配置必须强制 DEBUG=False，不接受环境变量覆盖"


@pytest.mark.prod_config
def test_prod_https_cookies_enabled(monkeypatch):
    prod = _load_prod(monkeypatch)
    assert prod.SESSION_COOKIE_SECURE is True
    assert prod.CSRF_COOKIE_SECURE is True
    assert prod.SECURE_SSL_REDIRECT is True


@pytest.mark.prod_config
def test_prod_hsts_and_headers(monkeypatch):
    prod = _load_prod(monkeypatch)
    assert prod.SECURE_HSTS_SECONDS > 0
    assert prod.SECURE_HSTS_INCLUDE_SUBDOMAINS is True
    assert prod.SECURE_CONTENT_TYPE_NOSNIFF is True
    assert prod.X_FRAME_OPTIONS == "DENY"
    assert prod.SESSION_COOKIE_HTTPONLY is True


@pytest.mark.prod_config
def test_prod_trusts_forwarded_proto(monkeypatch):
    """在 Nginx 反代后必须能从 X-Forwarded-Proto 识别原始协议。"""
    prod = _load_prod(monkeypatch)
    assert prod.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")


@pytest.mark.prod_config
def test_prod_uses_postgres(monkeypatch):
    prod = _load_prod(monkeypatch)
    assert prod.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"


# ---------------------------------------------------------------------------
# 失败路径：配置缺失必须报错，不得静默降级
# ---------------------------------------------------------------------------

@pytest.mark.prod_config
def test_prod_without_secret_key_fails(monkeypatch):
    with pytest.raises(RuntimeError) as exc:
        _load_prod(monkeypatch, DJANGO_SECRET_KEY="")
    assert "DJANGO_SECRET_KEY" in str(exc.value)


@pytest.mark.prod_config
def test_prod_rejects_short_secret_key(monkeypatch):
    with pytest.raises(RuntimeError) as exc:
        _load_prod(monkeypatch, DJANGO_SECRET_KEY="too-short")
    assert "太短" in str(exc.value) or "40" in str(exc.value)


@pytest.mark.prod_config
def test_prod_rejects_placeholder_secret_key(monkeypatch):
    with pytest.raises(RuntimeError):
        _load_prod(monkeypatch, DJANGO_SECRET_KEY="changeme-" + "y" * 60)


@pytest.mark.prod_config
def test_prod_without_allowed_hosts_fails(monkeypatch):
    with pytest.raises(RuntimeError) as exc:
        _load_prod(monkeypatch, DJANGO_ALLOWED_HOSTS="")
    assert "ALLOWED_HOSTS" in str(exc.value)


@pytest.mark.prod_config
def test_prod_without_csrf_trusted_origins_fails(monkeypatch):
    with pytest.raises(RuntimeError) as exc:
        _load_prod(monkeypatch, DJANGO_CSRF_TRUSTED_ORIGINS="")
    assert "CSRF_TRUSTED_ORIGINS" in str(exc.value)


@pytest.mark.prod_config
def test_prod_rejects_csrf_origin_without_scheme(monkeypatch):
    with pytest.raises(RuntimeError) as exc:
        _load_prod(monkeypatch, DJANGO_CSRF_TRUSTED_ORIGINS="stat.example.com")
    assert "协议前缀" in str(exc.value)


@pytest.mark.prod_config
def test_prod_rejects_sqlite(monkeypatch):
    """明确禁止在生产使用 SQLite —— 防止为了"跑得起来"而降级数据库。"""
    with pytest.raises(RuntimeError) as exc:
        _load_prod(monkeypatch, DJANGO_DB_ENGINE="django.db.backends.sqlite3")
    assert "SQLite" in str(exc.value)


@pytest.mark.prod_config
def test_prod_without_db_password_fails(monkeypatch):
    with pytest.raises(RuntimeError) as exc:
        _load_prod(monkeypatch, POSTGRES_PASSWORD="")
    assert "POSTGRES_PASSWORD" in str(exc.value)


@pytest.mark.prod_config
def test_prod_without_db_host_fails(monkeypatch):
    with pytest.raises(RuntimeError):
        _load_prod(monkeypatch, POSTGRES_HOST="")


# ---------------------------------------------------------------------------
# 仓库卫生：密钥不得进仓库
# ---------------------------------------------------------------------------

def test_env_file_is_gitignored():
    gitignore = (BASE_DIR / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore
    assert "!.env.example" in gitignore, "必须显式放行 .env.example"


def test_env_example_has_no_real_secret():
    text = (BASE_DIR / ".env.example").read_text(encoding="utf-8")
    assert "DJANGO_SECRET_KEY=\n" in text or "DJANGO_SECRET_KEY=" in text
    # 样例文件里不允许出现长随机串
    for line in text.splitlines():
        if line.startswith("DJANGO_SECRET_KEY="):
            assert line.strip() == "DJANGO_SECRET_KEY=", \
                ".env.example 中不得填入真实密钥"


def test_settings_modules_contain_no_hardcoded_secret():
    """配置模块里不得出现写死的密钥或默认密码。"""
    for name in ("base.py", "dev.py", "prod.py"):
        text = (BASE_DIR / "config" / "settings" / name).read_text(encoding="utf-8")
        for bad in ('SECRET_KEY = "', "SECRET_KEY = '", 'PASSWORD": "pingpong'):
            assert bad not in text, "%s 中疑似写死了敏感值：%s" % (name, bad)
