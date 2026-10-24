"""登录限流、可信 IP 与生产健康检查回归。"""
import pytest
from django.conf import settings
from django.test import Client, override_settings

@pytest.mark.django_db
def test_only_one_authentication_backend_is_enabled():
    assert settings.AUTHENTICATION_BACKENDS == ["accounts.backends.AccountBackend"]


@pytest.mark.django_db
@override_settings(
    LOGIN_RATE_LIMIT_ATTEMPTS=2,
    LOGIN_RATE_LIMIT_WINDOW_SECONDS=900,
    LOGIN_RATE_LIMIT_LOCK_SECONDS=300,
)
def test_login_is_throttled_per_account_and_ip(make_user):
    user = make_user(account="rate-limit-user", must_change_password=False, real_name="限流测试")
    client = Client(raise_request_exception=False)
    url = "/accounts/login/"
    for _ in range(2):
        client.post(url, {
            "username": user.account, "password": "wrong-password",
        }, REMOTE_ADDR="203.0.113.10", HTTP_HOST="127.0.0.1")

    blocked = client.post(url, {
        "username": user.account, "password": "TestPassw0rd!2026",
    }, REMOTE_ADDR="203.0.113.10", HTTP_HOST="127.0.0.1")
    assert blocked.status_code == 200
    assert "登录尝试次数过多" in blocked.content.decode("utf-8")

    other_ip = Client(raise_request_exception=False).post(url, {
        "username": user.account, "password": "TestPassw0rd!2026",
    }, REMOTE_ADDR="203.0.113.11", HTTP_HOST="127.0.0.1")
    assert other_ip.status_code == 302


@pytest.mark.django_db
def test_invalid_forwarded_ip_does_not_break_login(make_user):
    user = make_user(account="bad-ip-user", must_change_password=False, real_name="IP测试")
    client = Client(raise_request_exception=False)
    response = client.post(
        "/accounts/login/",
        {"username": user.account, "password": "TestPassw0rd!2026"},
        REMOTE_ADDR="127.0.0.1",
        HTTP_X_FORWARDED_FOR="not-an-ip",
        HTTP_HOST="127.0.0.1",
    )
    assert response.status_code == 302


@pytest.mark.django_db
@override_settings(TRUST_PROXY_HEADERS=True)
def test_trusted_proxy_real_ip_is_recorded(make_user):
    user = make_user(account="proxy-user", must_change_password=False, real_name="代理测试")
    client = Client(raise_request_exception=False)
    response = client.post(
        "/accounts/login/",
        {"username": user.account, "password": "TestPassw0rd!2026"},
        REMOTE_ADDR="172.18.0.5",
        HTTP_X_REAL_IP="203.0.113.20",
        HTTP_X_FORWARDED_FOR="203.0.113.20",
        HTTP_HOST="127.0.0.1",
    )
    assert response.status_code == 302
    user.refresh_from_db()
    assert str(user.last_login_ip) == "203.0.113.20"


@pytest.mark.django_db
@override_settings(SECURE_SSL_REDIRECT=True, SECURE_REDIRECT_EXEMPT=[r"^health/"])
def test_health_check_is_not_tls_redirected(client):
    response = client.get("/health/ready", HTTP_HOST="127.0.0.1")
    assert response.status_code == 200
