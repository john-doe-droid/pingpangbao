"""
比赛领域模型。

关系概览：
    Competition ──1:N──> CompetitionTask（在 tasks app）
    Competition ──1:N──> ReviewerAssignment（本文件）

设计约定：
- 比赛状态用明确的 choices 常量表达，不用魔法字符串；
- 归档不物理删除：所有既有数据保留，仅停止新增工作记录；
- 审核权是「比赛 × 用户」的显式授权关系，不由任何全局角色推导。
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class CompetitionStatus(models.TextChoices):
    """比赛状态。"""

    DRAFT = "draft", "草稿"
    ACTIVE = "active", "进行中"
    ARCHIVED = "archived", "已归档"


class CompetitionQuerySet(models.QuerySet):
    def draft(self):
        return self.filter(status=CompetitionStatus.DRAFT)

    def active(self):
        return self.filter(status=CompetitionStatus.ACTIVE)

    def archived(self):
        return self.filter(status=CompetitionStatus.ARCHIVED)

    def visible_to_members(self):
        """干事可见的比赛：草稿尚未开放，进行中与已归档都可查看。"""
        return self.exclude(status=CompetitionStatus.DRAFT)

    def open_for_tasks(self):
        """可以新增任务/分配的比赛：只有「进行中」。

        归档后停止新增工作记录；草稿尚未配置。
        """
        return self.filter(status=CompetitionStatus.ACTIVE)


class Competition(models.Model):
    """一场比赛（业务上的一次活动，例如"2026 秋季新生杯"）。"""

    name = models.CharField("比赛名称", max_length=120)
    description = models.TextField("比赛说明", blank=True)
    event_date = models.DateField("比赛日期")

    default_deadline = models.DateTimeField(
        "默认提交截止时间",
        help_text="任务未单独设置截止时间时，一律使用这个时间",
    )

    status = models.CharField(
        "状态", max_length=16,
        choices=CompetitionStatus.choices,
        default=CompetitionStatus.DRAFT,
        db_index=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="创建人",
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_competitions",
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    archived_at = models.DateTimeField("归档时间", null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="归档操作人",
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="archived_competitions",
    )

    objects = CompetitionQuerySet.as_manager()

    class Meta:
        verbose_name = "比赛"
        verbose_name_plural = "比赛"
        ordering = ["-event_date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(name=""),
                name="competition_name_not_empty",
            ),
            # 已归档必须有归档时间：避免出现「状态是已归档但查不到谁归档的」
            models.CheckConstraint(
                condition=(
                    Q(status="archived", archived_at__isnull=False)
                    | ~Q(status="archived")
                ),
                name="competition_archived_has_timestamp",
            ),
        ]

    def __str__(self):
        return self.name

    # --- 状态判断 ---

    @property
    def is_archived(self):
        return self.status == CompetitionStatus.ARCHIVED

    @property
    def is_active(self):
        return self.status == CompetitionStatus.ACTIVE

    @property
    def is_draft(self):
        return self.status == CompetitionStatus.DRAFT

    @property
    def can_modify_tasks(self):
        """能否在本场比赛下新增任务或人员分配。"""
        return self.status == CompetitionStatus.ACTIVE

    def clean(self):
        super().clean()
        if self.status == CompetitionStatus.ARCHIVED and self.archived_at is None:
            raise ValidationError({"status": "归档比赛必须同时记录归档时间。"})
        if self.status != CompetitionStatus.ARCHIVED and self.archived_at is not None:
            # 允许从归档恢复？本阶段不允许，避免绕过归档授权的口子
            raise ValidationError({"status": "只有已归档的比赛才应有归档时间。"})

    # --- 状态流转（业务动作集中在这里，便于测试与授权检查）---

    def archive(self, by_user):
        """归档。只在服务层调用，授权检查由视图负责。"""
        if self.is_archived:
            return False
        self.status = CompetitionStatus.ARCHIVED
        self.archived_at = timezone.now()
        self.archived_by = by_user
        self.save(update_fields=["status", "archived_at", "archived_by", "updated_at"])
        return True


class ReviewerAssignment(models.Model):
    """比赛 ↔ 审核人 的授权关系。

    关键约束（对应验收要求）：
    - 一场比赛可授权多名审核人（多条记录，不同 reviewer）；
    - 同一用户对同一场比赛只能有一条**有效**授权（部分唯一索引保证）；
    - 审核权只能来自本表，不能由普通登录身份或管理身份推导。
    """

    competition = models.ForeignKey(
        Competition,
        verbose_name="比赛",
        on_delete=models.CASCADE,
        related_name="reviewer_assignments",
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="审核人",
        on_delete=models.CASCADE,
        related_name="review_assignments",
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="授权人",
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="granted_review_assignments",
    )
    granted_at = models.DateTimeField("授权时间", auto_now_add=True)
    revoked_at = models.DateTimeField("撤销时间", null=True, blank=True)
    is_active = models.BooleanField("当前有效", default=True, db_index=True)
    note = models.CharField("备注", max_length=200, blank=True)

    class Meta:
        verbose_name = "审核人授权"
        verbose_name_plural = "审核人授权"
        ordering = ["-granted_at", "-id"]
        constraints = [
            # 同一比赛 + 同一审核人，只能有一条有效授权。
            # 用部分唯一索引（而不是简单 unique_together），
            # 这样撤销后可以重新授权，历史记录依然保留。
            models.UniqueConstraint(
                fields=["competition", "reviewer"],
                condition=Q(is_active=True),
                name="uniq_active_reviewer_per_competition",
            ),
        ]
        indexes = [
            models.Index(fields=["competition", "is_active"]),
            models.Index(fields=["reviewer", "is_active"]),
        ]

    def __str__(self):
        return "%s → %s" % (self.competition.name, self.reviewer.get_short_name() if self.reviewer else "?")

    def revoke(self):
        """撤销授权：置为无效并记录时间，不删除记录。"""
        if not self.is_active:
            return False
        self.is_active = False
        self.revoked_at = timezone.now()
        self.save(update_fields=["is_active", "revoked_at"])
        return True
