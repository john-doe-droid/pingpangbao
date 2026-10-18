"""健康检查测试（对应验收 E 与"不泄露敏感信息"）。"""
import json

import pytest


def test_live_returns_200_without_db(client, django_db_blocker):
    """存活探针不应访问数据库：即使数据库被阻断也必须返回 200。"""
    with django_db_blocker.block():
        resp = client.get("/health/live")
    assert resp.status_code == 200
    payload = json.loads(resp.content)
    assert payload["status"] == "ok"
    assert payload["check"] == "live"


def test_ready_returns_200_when_db_available(client, db):
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    payload = json.loads(resp.content)
    assert payload["status"] == "ok"
    assert payload["database"] == "ok"


def test_ready_returns_503_when_db_unavailable(client, monkeypatch):
    """数据库不可用时应返回 503，而不是假装健康。"""
    from django.db import connection

    class _Boom:
        def __enter__(self):
            raise RuntimeError("模拟数据库不可用：连接被拒绝")

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(connection, "cursor", lambda *a, **k: _Boom())

    resp = client.get("/health/ready")
    assert resp.status_code == 503
    payload = json.loads(resp.content)
    assert payload["status"] == "unavailable"
    assert payload["database"] == "error"


@pytest.mark.parametrize("path", ["/health/live", "/health/ready"])
def test_health_does_not_leak_secrets(client, db, settings, path):
    """健康检查响应不得出现密钥、密码、主机名或内部堆栈。"""
    resp = client.get(path)
    body = resp.content.decode("utf-8").lower()

    forbidden = [
        str(settings.SECRET_KEY).lower(),
        str(settings.DATABASES["default"].get("PASSWORD", "")).lower(),
        str(settings.DATABASES["default"].get("NAME", "")).lower(),
        "traceback",
        "django.db.utils",
        "psycopg",
        "password",
        "secret",
    ]
    for token in forbidden:
        if token in ("", "none"):
            continue
        assert token not in body, "健康检查响应泄露了敏感内容：%r" % token


def test_ready_error_message_is_generic(client, monkeypatch):
    """失败响应里的说明必须是面向用户的通用文案，不含技术细节。"""
    from django.db import connection

    class _Boom:
        def __enter__(self):
            raise RuntimeError("FATAL: password authentication failed for user 'pingpong'")

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(connection, "cursor", lambda *a, **k: _Boom())
    resp = client.get("/health/ready")
    body = resp.content.decode("utf-8")

    assert "password authentication failed" not in body
    assert "pingpong" not in body
    assert "无法解析" not in body
