"""
任务领域模型。

核心实体与关系：
    WorkType        工作类型字典（可页面维护，停用而非删除）
    TaskTemplate    任务模板（定义"一类工作要交什么材料"）
    CompetitionTask 比赛任务（模板在某场比赛中的实例，带规则快照）
    TaskAssignment  人员分配（任务 × 干事，有效记录唯一）

关键设计：
1. **规则快照**：CompetitionTask 创建时把模板当时的材料要求复制成自己的字段，
   之后模板被改不会影响既有任务。
2. **部分唯一索引**：同一任务 + 同一干事只能有一条有效分配；
   撤销/停用后历史保留，可重新分配。
3. **不做物理删除**：工作类型、模板都走"停用"，保证历史记录不失分类。
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


# ---------------------------------------------------------------------------
# 工作类型
# ---------------------------------------------------------------------------

class WorkType(models.Model):
    """工作类型字典。

    刻意做成数据表而不是 Python 枚举：新增「视频剪辑」这类类型
    只需管理员在页面上加一条，不需要改代码、不需要重新部署。
    """

    name = models.CharField("工作类型名称", max_length=60, unique=True)
    description = models.CharField("说明", max_length=200, blank=True)
    sort_order = models.IntegerField("排序", default=100)

    is_active = models.BooleanField(
        "启用", default=True, db_index=True,
        help_text="停用后不能被新任务选用，但已有任务与历史记录不受影响",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="创建人",
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_work_types",
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "工作类型"
        verbose_name_plural = "工作类型"
        ordering = ["sort_order", "name"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(name=""), name="worktype_name_not_empty",
            ),
        ]

    def __str__(self):
        return self.name

    def deactivate(self):
        self.is_active = False
        self.save(update_fields=["is_active", "updated_at"])

    def activate(self):
        self.is_active = True
        self.save(update_fields=["is_active", "updated_at"])


# ---------------------------------------------------------------------------
# 任务模板
# ---------------------------------------------------------------------------

class TaskTemplate(models.Model):
    """任务模板：定义一类工作要提交什么材料。

    材料体系：
        text    —— 文字说明
        image   —— 图片证明
        file    —— 文件附件
        link    —— 作品链接

    每类材料有两个开关：
        allow_<x>    是否允许提交
        require_<x>  是否必填
    校验规则：必填必然意味着允许（require ⇒ allow），否则模板自相矛盾。
    """

    MATERIAL_FIELDS = ("text", "image", "file", "link")

    name = models.CharField("模板名称", max_length=120)
    work_type = models.ForeignKey(
        WorkType,
        verbose_name="工作类型",
        on_delete=models.PROTECT,          # 被引用时不允许删除，避免历史失去分类
        related_name="templates",
    )
    description = models.TextField("任务说明", blank=True)

    # --- 文字与图片（任务要求里显式点名的两项，默认必填）---
    allow_text = models.BooleanField("允许文字说明", default=True)
    require_text = models.BooleanField("要求文字说明", default=True)

    allow_image = models.BooleanField("允许图片证明", default=True)
    require_image = models.BooleanField("要求图片证明", default=True)

    # --- 其它材料（默认不允许，按需开启）---
    allow_file = models.BooleanField("允许文件附件", default=False)
    require_file = models.BooleanField("要求文件附件", default=False)

    allow_link = models.BooleanField("允许作品链接", default=False)
    require_link = models.BooleanField("要求作品链接", default=False)

    max_records_per_member = models.PositiveSmallIntegerField(
        "每名干事允许创建的工作记录数量", default=1,
        validators=[],
        help_text="正整数；默认 1，可按需调整",
    )

    is_active = models.BooleanField(
        "启用", default=True, db_index=True,
        help_text="停用后不能被新任务选用，已创建的任务不受影响",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="创建人",
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_task_templates",
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "任务模板"
        verbose_name_plural = "任务模板"
        ordering = ["-updated_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(name=""), name="template_name_not_empty",
            ),
            # 提交次数必须是正整数（0 或负数没有业务含义）
            models.CheckConstraint(
                condition=Q(max_records_per_member__gte=1),
                name="template_max_records_positive",
            ),
            # 必填 ⇒ 允许。数据库层再兜一道，避免绕过表单写入非法组合。
            models.CheckConstraint(
                condition=Q(require_text=False) | Q(allow_text=True),
                name="template_require_text_implies_allow",
            ),
            models.CheckConstraint(
                condition=Q(require_image=False) | Q(allow_image=True),
                name="template_require_image_implies_allow",
            ),
            models.CheckConstraint(
                condition=Q(require_file=False) | Q(allow_file=True),
                name="template_require_file_implies_allow",
            ),
            models.CheckConstraint(
                condition=Q(require_link=False) | Q(allow_link=True),
                name="template_require_link_implies_allow",
            ),
        ]

    def __str__(self):
        return self.name

    # --- 材料要求的统一读取接口（供表单校验与模板渲染共用）---

    def material_matrix(self):
        """返回 [(名称, 中文标签, 是否允许, 是否必填), ...]。"""
        labels = {
            "text": "文字说明",
            "image": "图片证明",
            "file": "文件附件",
            "link": "作品链接",
        }
        rows = []
        for key in self.MATERIAL_FIELDS:
            rows.append((
                key, labels[key],
                getattr(self, "allow_%s" % key),
                getattr(self, "require_%s" % key),
            ))
        return rows

    def clean(self):
        """模板配置合法性校验。

        规则 1：某种材料被设为必填，就必须同时允许提交。
        规则 2：提交次数必须是正整数。
        """
        super().clean()
        errors = {}

        for key, label, allow, require in self.material_matrix():
            if require and not allow:
                errors["allow_%s" % key] = "%s被设为必填，必须同时允许提交。" % label

        count = self.max_records_per_member
        if count is None or int(count) < 1:
            errors["max_records_per_member"] = "提交次数必须是正整数（至少 1）。"

        if errors:
            raise ValidationError(errors)

    def rule_snapshot(self):
        """把当前材料规则冻结成一份可存储的快照。

        CompetitionTask 创建时调用它，之后模板再改也不会影响既有任务。
        """
        return {
            "max_records_per_member": self.max_records_per_member,
            "allow_text": self.allow_text, "require_text": self.require_text,
            "allow_image": self.allow_image, "require_image": self.require_image,
            "allow_file": self.allow_file, "require_file": self.require_file,
            "allow_link": self.allow_link, "require_link": self.require_link,
        }

    def snapshot_summary(self):
        """快照的可读摘要，用于页面展示"本任务实际生效的材料要求"。"""
        parts = []
        for _key, label, allow, require in self.material_matrix():
            if require:
                parts.append("%s（必填）" % label)
            elif allow:
                parts.append("%s（选填）" % label)
        return "、".join(parts) if parts else "无需提交材料"

    def deactivate(self):
        self.is_active = False
        self.save(update_fields=["is_active", "updated_at"])

    def activate(self):
        self.is_active = True
        self.save(update_fields=["is_active", "updated_at"])


# ---------------------------------------------------------------------------
# 比赛任务
# ---------------------------------------------------------------------------

class CompetitionTask(models.Model):
    """某场比赛下的一个具体任务。

    管理员可以直接填写材料要求，也可以从模板快速带出配置。无论采用哪种
    方式，创建时都会把规则**快照**进自身字段，因此模板后续被修改不会
    悄悄改变已发布任务的规则。
    """

    competition = models.ForeignKey(
        "competitions.Competition",
        verbose_name="所属比赛",
        on_delete=models.CASCADE,
        related_name="tasks",
    )
    template = models.ForeignKey(
        TaskTemplate,
        verbose_name="来源模板",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="competition_tasks",
        help_text="可为空；空值表示直接自定义任务",
    )

    # --- 从模板复制来的展示信息（可微调，不影响快照规则）---
    name = models.CharField("任务名称", max_length=120)
    work_type = models.ForeignKey(
        WorkType,
        verbose_name="工作类型",
        on_delete=models.PROTECT,
        related_name="competition_tasks",
    )
    description = models.TextField("任务说明", blank=True)

    # --- 规则快照：创建时冻结，不随后续模板修改而变 ---
    rule_snapshot = models.JSONField(
        "生效规则快照", default=dict,
        help_text="创建任务时从模板复制的材料要求与提交次数，之后不再跟随模板变化",
    )

    # --- 可用性冗余字段（便于查询与页面展示；与快照保持一致）---
    max_records_per_member = models.PositiveSmallIntegerField(
        "每名干事提交次数限制", default=1,
    )

    deadline = models.DateTimeField(
        "任务独立截止时间", null=True, blank=True,
        help_text="留空则继承比赛默认截止时间；填写后以本任务的为准",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="创建人",
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_competition_tasks",
    )
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "比赛任务"
        verbose_name_plural = "比赛任务"
        ordering = ["competition", "id"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(name=""), name="task_name_not_empty",
            ),
            models.CheckConstraint(
                condition=Q(max_records_per_member__gte=1),
                name="task_max_records_positive",
            ),
            # 同一比赛下，同一模板不重复创建任务（避免管理员误点两次）
            models.UniqueConstraint(
                fields=["competition", "template"],
                name="uniq_task_per_competition_template",
            ),
        ]
        indexes = [
            models.Index(fields=["competition"]),
        ]

    def __str__(self):
        return "%s · %s" % (self.competition.name, self.name)

    # --- 截止时间继承 ---

    @property
    def effective_deadline(self):
        """生效截止时间：任务自身优先，未设置则继承比赛默认值。"""
        return self.deadline or self.competition.default_deadline

    @property
    def deadline_source(self):
        return "任务独立设置" if self.deadline else "继承自比赛"

    def is_overdue(self, at=None):
        """是否已逾期（相对当前时间或指定时间）。"""
        at = at or timezone.now()
        return self.effective_deadline < at

    # --- 快照规则的可读接口 ---

    def snapshot_material_rows(self):
        """按快照返回 [(标签, 是否允许, 是否必填), ...]。"""
        labels = {
            "text": "文字说明", "image": "图片证明", "file": "文件附件",
            "link": "作品链接",
        }
        rows = []
        for key, label in labels.items():
            allow = bool(self.rule_snapshot.get("allow_%s" % key))
            require = bool(self.rule_snapshot.get("require_%s" % key))
            if allow or require:
                rows.append((label, allow, require))
        return rows

    def snapshot_summary(self):
        """快照的可读摘要（供页面与操作记录使用）。

        直接读 rule_snapshot 而不是读模板，保证展示的是
        「本任务实际生效的规则」，而不是模板的当前状态。
        """
        parts = []
        for label, allow, require in self.snapshot_material_rows():
            if require:
                parts.append("%s（必填）" % label)
            elif allow:
                parts.append("%s（选填）" % label)
        summary = "、".join(parts) if parts else "无需提交材料"
        return "%s；每人 %d 份" % (summary, self.max_records_per_member)

    @classmethod
    def build_from_template(cls, competition, template, *, name=None,
                            description=None, deadline=None, created_by=None):
        """从模板创建任务，并冻结规则快照。

        这是**唯一**推荐的任务创建入口：它保证快照与可用性字段
        始终来自模板的当前状态，不会出现"忘了复制规则"的漏项。
        """
        snapshot = template.rule_snapshot()
        return cls(
            competition=competition,
            template=template,
            name=name or template.name,
            work_type=template.work_type,
            description=description if description is not None else template.description,
            rule_snapshot=snapshot,
            max_records_per_member=snapshot["max_records_per_member"],
            deadline=deadline,
            created_by=created_by,
        )

    def clean(self):
        super().clean()
        errors = {}
        if self.competition_id and self.competition.is_archived:
            # 归档比赛不得新增任务（编辑既有任务也不应依赖本校验，
            # 视图层会拒绝所有对归档比赛的写操作）
            errors["competition"] = "已归档的比赛不能再新建或修改任务。"
        if self.template_id and self.work_type_id \
                and self.template.work_type_id != self.work_type_id:
            # 允许管理员微调工作类型，但必须显式，防止不小心写错
            pass
        if errors:
            raise ValidationError(errors)


# ---------------------------------------------------------------------------
# 人员分配
# ---------------------------------------------------------------------------

class TaskAssignment(models.Model):
    """任务 ↔ 干事 的分配关系。

    约束：
    - 同一任务可分配给多名干事（多条记录，不同 member）；
    - 同一任务不能对同一干事存在两条**有效**记录（部分唯一索引）；
    - 已产生工作记录后不允许删除分配（本阶段用 is_active 停用，
      后续阶段接入工作记录后会再加服务层校验）。
    """

    task = models.ForeignKey(
        CompetitionTask,
        verbose_name="比赛任务",
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="干事",
        on_delete=models.CASCADE,
        related_name="task_assignments",
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="分配人",
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="made_task_assignments",
    )
    assigned_at = models.DateTimeField("分配时间", auto_now_add=True)

    is_active = models.BooleanField(
        "当前有效", default=True, db_index=True,
        help_text="停用后不再出现在干事的任务列表，但历史记录保留",
    )
    deactivated_at = models.DateTimeField("停用时间", null=True, blank=True)
    note = models.CharField("备注", max_length=200, blank=True)

    class Meta:
        verbose_name = "任务分配"
        verbose_name_plural = "任务分配"
        ordering = ["-assigned_at", "-id"]
        constraints = [
            # 同一任务 + 同一干事只能有一条有效分配。
            # 用部分唯一索引：停用后可重新分配，历史不丢。
            models.UniqueConstraint(
                fields=["task", "member"],
                condition=Q(is_active=True),
                name="uniq_active_assignment_per_task_member",
            ),
        ]
        indexes = [
            models.Index(fields=["member", "is_active"]),
            models.Index(fields=["task", "is_active"]),
        ]

    def __str__(self):
        return "%s → %s" % (self.task, self.member.get_short_name() if self.member else "?")

    def deactivate(self):
        """停用分配（不删除）。"""
        if not self.is_active:
            return False
        self.is_active = False
        self.deactivated_at = timezone.now()
        self.save(update_fields=["is_active", "deactivated_at"])
        return True

    @property
    def effective_deadline(self):
        return self.task.effective_deadline
