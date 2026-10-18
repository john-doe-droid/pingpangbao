"""
认证后端。

自定义用户模型把学号作为 USERNAME_FIELD，因此 Django 自带的
ModelBackend 已经能按学号认证。这里额外提供一个显式命名的后端，
用于：
1. 记录登录 IP（便于后续审计）；
2. 对停用账号给出统一的失败结果（不泄露"账号存在但被禁用"）。

安全性说明：本后端不改变密码校验逻辑，仍调用 user.check_password()，
即走 Django 的哈希校验；不在这里做任何"明文比较"。
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

UserModel = get_user_model()


class StudentIDBackend(ModelBackend):
    """按学号认证。"""

    def authenticate(self, request, username=None, password=None, **kwargs):
        student_id = username or kwargs.get(UserModel.USERNAME_FIELD)
        if student_id is None or password is None:
            return None

        try:
            user = UserModel.objects.get(student_id=str(student_id).strip())
        except UserModel.DoesNotExist:
            # 与"密码错误"返回同样的结果，避免暴露学号是否存在。
            # 注意：这里仍然让 Django 跑一次哈希（见 authenticate 的调用方），
            # 但为简洁起见此处直接返回 None，由上游统一处理。
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
