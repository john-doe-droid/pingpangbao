"""
认证后端。

自定义用户模型把账号作为 USERNAME_FIELD。这里只保留一个认证后端，
账号不存在时也执行一次密码哈希，避免通过响应时间判断账号是否存在。

安全性说明：本后端不改变密码校验逻辑，仍调用 user.check_password()，
即走 Django 的哈希校验；不在这里做任何"明文比较"。
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

UserModel = get_user_model()


class AccountBackend(ModelBackend):
    """按账号认证，并保持账号存在与否的哈希耗时一致。"""

    def authenticate(self, request, username=None, password=None, **kwargs):
        account = username or kwargs.get(UserModel.USERNAME_FIELD)
        if account is None or password is None:
            return None

        try:
            user = UserModel._default_manager.get_by_natural_key(str(account).strip())
        except UserModel.DoesNotExist:
            # Django 的推荐做法：不存在账号也跑一次哈希，缩小计时差异。
            UserModel().set_password(password)
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
