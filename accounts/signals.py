"""认证相关信号。"""
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .security import sync_session_security_version


@receiver(user_logged_in)
def record_session_security_version(sender, request, user, **kwargs):
    """记录本次登录对应的会话版本。"""
    if request is not None:
        sync_session_security_version(request, user)
