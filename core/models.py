"""
core 的公共模型。

P2 新增：AuditLog —— 重要管理操作的操作记录。
放在 core 而不是业务 app 里，因为它是跨模块的公共基础能力
（P1 已把 core 定义为「健康检查及公共基础功能」的归属地）。

这是**纯新增**文件：P1 阶段 core 没有任何模型，
因此这里新增模型与迁移不会影响 P1 的任何既有结构。
"""
from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """重要管理操作记录。

    记录：谁、在什么时候、对什么对象、做了什么。
    用于事后追溯（例如"比赛的审核授权是谁改的"）。

    设计取舍：
    - actor 用 SET_NULL：即使用户被停用甚至移除，操作记录本身必须留存；
    - 目标对象用「类型 + 主键 + 可读描述」三元组保存，而不是外键，
      这样目标对象即使后续被停用或结构调整，历史记录也不会失效。
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="操作人",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="audit_logs",
        help_text="操作人；账号被移除时保留为 NULL，但记录不删除",
    )
    action = models.CharField(
        "动作", max_length=80, db_index=True,
        help_text="例如 competition.create / reviewer.grant",
    )
    target_type = models.CharField("对象类型", max_length=80, blank=True)
    target_id = models.CharField("对象主键", max_length=64, blank=True)
    target_repr = models.CharField("对象描述", max_length=200, blank=True)
    detail = models.TextField("补充说明", blank=True)
    ip_address = models.GenericIPAddressField("来源 IP", null=True, blank=True)

    created_at = models.DateTimeField("时间", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "操作记录"
        verbose_name_plural = "操作记录"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["target_type", "target_id"]),
        ]

    def __str__(self):
        return "%s %s %s" % (self.created_at, self.action, self.target_repr)
