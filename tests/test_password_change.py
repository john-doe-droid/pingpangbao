"""首登强制改密约束测试（对应验收 B 的"首次登录强制修改初始密码"）。"""
from django.contrib.auth import get_user_model

User = get_user_model()


def test_new_user_defaults_to_must_change_password(db):
    """管理员新建的用户默认必须改密。"""
    user = User.objects.create_user(account="2025317020104", password="TestPassw0rd!2026")
    assert user.must_change_password is True
    assert user.password_changed_at is None


def test_first_login_redirects_to_password_change(client, make_user, plain_password):
    """首登后应被引导到改密页，而不是进入主页。"""
    make_user(account="2025317020104", password=plain_password)
    resp = client.post(
        "/accounts/login/",
        {"username": "2025317020104", "password": plain_password},
    )
    assert resp.status_code == 302
    assert resp.url == "/accounts/password/change/"


def test_middleware_blocks_other_pages_until_changed(client, make_user, plain_password):
    """未改密前访问其它页面应被重定向回改密页。"""
    make_user(account="2025317020104", password=plain_password)
    client.login(username="2025317020104", password=plain_password)

    resp = client.get("/")
    assert resp.status_code == 302
    assert resp.url == "/accounts/password/change/"


def test_health_endpoints_exempt_from_force_change(client, make_user, plain_password):
    """改密白名单内的健康检查不应被拦截（否则容器探针会失败）。"""
    make_user(account="2025317020104", password=plain_password)
    client.login(username="2025317020104", password=plain_password)

    assert client.get("/health/live").status_code == 200


def test_password_change_clears_flag(client, make_user, plain_password, new_password):
    make_user(account="2025317020104", password=plain_password)
    client.login(username="2025317020104", password=plain_password)

    resp = client.post(
        "/accounts/password/change/",
        {"new_password1": new_password, "new_password2": new_password},
    )
    assert resp.status_code == 302
    assert resp.url == "/"

    user = User.objects.get(account="2025317020104")
    assert user.must_change_password is False
    assert user.password_changed_at is not None
    assert user.check_password(new_password) is True
    assert user.check_password(plain_password) is False


def test_after_change_user_can_reach_home(client, make_user, plain_password, new_password):
    make_user(account="2025317020104", password=plain_password)
    client.login(username="2025317020104", password=plain_password)
    client.post(
        "/accounts/password/change/",
        {"new_password1": new_password, "new_password2": new_password},
    )
    resp = client.get("/")
    assert resp.status_code == 302
    assert resp.url == "/my/tasks/"
    assert client.get("/", follow=True).status_code == 200


def test_cannot_reuse_same_password(client, make_user, plain_password):
    """新密码不得与当前密码相同（表单自定义校验）。"""
    make_user(account="2025317020104", password=plain_password)
    client.login(username="2025317020104", password=plain_password)

    resp = client.post(
        "/accounts/password/change/",
        {"new_password1": plain_password, "new_password2": plain_password},
    )
    assert resp.status_code == 200, "应留在改密页而不是跳转"
    assert "不能与当前密码相同" in resp.content.decode("utf-8")


def test_weak_password_rejected(client, make_user, plain_password):
    """过短/纯数字密码应被 Django 密码校验器拒绝。"""
    make_user(account="2025317020104", password=plain_password)
    client.login(username="2025317020104", password=plain_password)

    resp = client.post(
        "/accounts/password/change/",
        {"new_password1": "12345678", "new_password2": "12345678"},
    )
    assert resp.status_code == 200
    user = User.objects.get(account="2025317020104")
    assert user.must_change_password is True, "改密失败时不应清除首登标记"
