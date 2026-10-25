"""accounts 路由。

刻意**不包含** register（自助注册）路由 —— 账号由管理员创建。
"""
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.StudentLoginView.as_view(), name="login"),
    path("logout/", views.StudentLogoutView.as_view(), name="logout"),
    path("profile/", views.profile, name="profile"),
    path("password/change/", views.password_change, name="password_change"),
]
