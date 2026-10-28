# -*- coding: utf-8 -*-
"""审核记录模型。

与既有日志体系的关系
--------------------
P2 已经在 `core.AuditLog` 里做了通用操作日志（谁、何时、对什么对象、做了什么）。
本模块**不重复造一套通用日志**，而是只建审核领域特有的记录：
它必须回答 AuditLog 答不了的几个问题 ——
    * 这次审核针对的是**哪个提交版本**（AuditLog 只有字符串 target_id）
    * 审核**动作**是 approve / reject / reopen / undo_reject
    * **意见**与**重新审核原因**
    * 该记录所属的**工作记录状态流转**（from → to）

因此 ReviewLog 与 AuditLog 是互补关系：
ReviewLog 负责业务语义与历史追溯，AuditLog 负责全局操作审计。
两者在服务层同时写入。

不可覆盖
--------
ReviewLog 一旦写入不提供任何修改入口（模型没有 update 相关方法，
视图也不提供编辑），且不使用 on_delete=CASCADE 到 Submission ——
工作记录若被删除（本阶段业务上不允许），审核历史仍需留存。
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class ReviewAction(models.TextChoices):
    """审核动作。"""

    APPROVE = "approve", "通过"
    REJECT = "reject", "退回修改"
    REOPEN = "reopen", "发起重新审核"
    UNDO_REJECT = "undo_reject", "撤销退回"


class ReviewLog(models.Model):
    """一次审核动作的不可变记录。

    「同一轮审核只能有一个有效结果」的实现方式：
    审核前在服务层用 `select_for_update` 锁住 Submission 行，
    读取当前状态并复核（例如退回要求状态为 pending），
    再写入本表并推进状态。并发的第二个请求会因为状态已变而失败，
    因此不会产生两个都生效的结果。
    """

    submission = models.ForeignKey(
        "submissions.Submission",
        verbose_name="工作记录",
        on_delete=models.PROTECT,      # 审核历史不因记录删除而消失
        related_name="review_logs",
    )
    version = models.ForeignKey(
        "submissions.SubmissionVersion",
        verbose_name="被审核的提交版本",
        on_delete=models.PROTECT,
        related_name="review_logs",
        help_text="审核针对的是具体某一版，而不是整条记录",
    )

    action = models.CharField(
        "审核动作", max_length=20, choices=ReviewAction.choices, db_index=True,
    )

    # 状态流转（便于事后核对，不必反推）
    from_status = models.CharField("原状态", max_length=20, blank=True)
    to_status = models.CharField("新状态", max_length=20, blank=True)

    comment = models.TextField(
        "审核意见", blank=True,
        help_text="退回时必填（服务层强制校验）",
    )
    reopen_reason = models.TextField(
        "重新审核原因", blank=True,
        help_text="仅发起重新审核时填写，且必填",
    )

    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="操作人",
        on_delete=models.PROTECT,      # 审核人账号变动不影响历史
        related_name="review_logs",
    )
    acted_at = models.DateTimeField("操作时间", default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "审核记录"
        verbose_name_plural = "审核记录"
        ordering = ["-acted_at", "-id"]
        constraints = [
            # 退回必须填理由 —— 数据库层兜一道，防止绕过服务层写入空理由
            models.CheckConstraint(
                condition=(
                    ~models.Q(action="reject") | ~models.Q(comment="")
                ),
                name="reject_requires_comment",
            ),
            # 重新审核必须填原因
            models.CheckConstraint(
                condition=(
                    ~models.Q(action="reopen") | ~models.Q(reopen_reason="")
                ),
                name="reopen_requires_reason",
            ),
        ]
        indexes = [
            models.Index(fields=["submission", "acted_at"]),
            models.Index(fields=["action"]),
        ]

    def __str__(self):
        return "%s %s（%s）" % (
            self.submission, self.get_action_display(),
            self.reviewer.get_short_name() if self.reviewer else "?",
        )

    @property
    def is_rejection(self):
        return self.action == ReviewAction.REJECT
