# -*- coding: utf-8 -*-
"""工作成果提交领域模型。

核心区分（这是 P3 最容易被做错的地方）
--------------------------------------
    Submission          「一条工作记录」—— 计数用它，**不是**用版本数
    SubmissionVersion   「第几次提交」—— 每次首提/退回后重提各产生一版
    Attachment          「某一次提交里的某份材料」—— **必须挂版本，不能挂记录**

为什么这样分：
  * 规则「每人每任务新增记录数受上限限制」按 Submission 数量计算，
    退回后重新提交**不**额外占用次数 —— 若把版本数当记录数就会算错；
  * 规则「旧版本内容与材料不可被新版本覆盖」要求附件挂在版本上 ——
    若挂在 Submission 上，重提时只能覆盖，历史就丢了。

并发控制
--------
提交次数与图片配额都在服务层用 `select_for_update` 行级锁保护，
不依赖前端按钮，也不用普通的先查后写（那在并发下必然超额）。
"""
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class SubmissionStatus(models.TextChoices):
    """工作记录状态机。"""

    PENDING = "pending", "待审核"
    NEEDS_REVISION = "needs_revision", "退回修改"
    APPROVED = "approved", "审核通过"


class MaterialType(models.TextChoices):
    """材料类型（与任务模板 rule_snapshot 的键一一对应）。"""

    TEXT = "text", "文字说明"
    IMAGE = "image", "图片证明"
    FILE = "file", "文件附件"
    LINK = "作品链接"
    VIDEO = "video", "视频"


class SubmissionQuerySet(models.QuerySet):
    def pending(self):
        return self.filter(status=SubmissionStatus.PENDING)

    def needs_revision(self):
        return self.filter(status=SubmissionStatus.NEEDS_REVISION)

    def approved(self):
        return self.filter(status=SubmissionStatus.APPROVED)

    def for_member(self, user):
        return self.filter(assignment__member=user)

    def for_competition(self, competition):
        return self.filter(assignment__task__competition=competition)


class Submission(models.Model):
    """一条工作记录。

    「新增记录次数」按本表的行数计算 —— 一条记录可以有多个版本，
    但只算一次新增。
    """

    assignment = models.ForeignKey(
        "tasks.TaskAssignment",
        verbose_name="任务分配",
        on_delete=models.PROTECT,      # 已产生记录的分配不允许删除（历史留存）
        related_name="submissions",
    )
    status = models.CharField(
        "当前状态", max_length=20,
        choices=SubmissionStatus.choices,
        default=SubmissionStatus.PENDING,
        db_index=True,
    )
    current_version = models.ForeignKey(
        "SubmissionVersion",
        verbose_name="当前有效版本",
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",              # 反向关系由 SubmissionVersion.submission 提供
        help_text="指向最新一次提交；历史版本不受影响",
    )

    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    objects = SubmissionQuerySet.as_manager()

    class Meta:
        verbose_name = "工作记录"
        verbose_name_plural = "工作记录"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["assignment", "status"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return "记录#%s（%s）" % (self.pk, self.get_status_display())

    # --- 便捷读取 ---

    @property
    def member(self):
        return self.assignment.member

    @property
    def task(self):
        return self.assignment.task

    @property
    def competition(self):
        return self.assignment.task.competition

    @property
    def is_approved(self):
        return self.status == SubmissionStatus.APPROVED

    @property
    def can_be_revised(self):
        """是否允许本人修改并重新提交（仅「退回修改」状态）。"""
        return self.status == SubmissionStatus.NEEDS_REVISION

    @property
    def edition_count(self):
        """已提交的版本数（用于页面显示「第 N 次提交」）。"""
        return self.versions.count()

    def latest_version(self):
        """最新版本（不依赖 current_version 指针，避免指针失效时读错）。"""
        return self.versions.order_by("-version_no").first()

    @property
    def is_overdue_now(self):
        """当前版本提交时是否逾期。"""
        version = self.current_version or self.latest_version()
        return bool(version and version.is_overdue)


class SubmissionVersion(models.Model):
    """一次具体提交的版本快照。

    版本号在同一 Submission 内**从 1 开始连续递增**，由
    `(submission, version_no)` 唯一约束保证不重复；
    连续性由服务层在行级锁内分配（见 submissions/services.py）。
    """

    submission = models.ForeignKey(
        Submission,
        verbose_name="所属工作记录",
        on_delete=models.CASCADE,
        related_name="versions",
    )
    version_no = models.PositiveIntegerField(
        "版本序号", help_text="同一记录内从 1 开始连续递增",
    )

    text_content = models.TextField("文字说明", blank=True)

    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="提交人",
        on_delete=models.PROTECT,      # 提交历史不允许因账号删除而消失
        related_name="submission_versions",
    )
    submitted_at = models.DateTimeField("提交时间", default=timezone.now)

    # 提交「当时」的生效截止时间与逾期标记 —— 冻结下来，
    # 之后任务/比赛截止时间被改也不影响历史判定
    effective_deadline = models.DateTimeField(
        "提交时生效的截止时间",
        help_text="提交瞬间从任务继承/自身设置计算得出，作为逾期判定的唯一依据",
    )
    is_overdue = models.BooleanField(
        "逾期提交", default=False,
        help_text="依据提交时刻与上述生效截止时间比较得出",
    )

    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "提交版本"
        verbose_name_plural = "提交版本"
        ordering = ["submission", "version_no"]
        constraints = [
            # 版本号在同一记录内唯一（配合服务层的连续分配，实现"连续且可核对"）
            models.UniqueConstraint(
                fields=["submission", "version_no"],
                name="uniq_version_no_per_submission",
            ),
            models.CheckConstraint(
                condition=Q(version_no__gte=1),
                name="version_no_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["submission", "version_no"]),
        ]

    def __str__(self):
        return "%s 第 %d 版" % (self.submission, self.version_no)

    # --- 材料便捷读取 ---

    def attachments_of(self, material_type):
        return self.attachments.filter(material_type=material_type)

    @property
    def image_attachments(self):
        """主图附件（不含缩略图记录）。"""
        return self.attachments.filter(material_type=MaterialType.IMAGE, is_thumbnail=False)

    @property
    def link_attachments(self):
        return self.attachments.filter(material_type=MaterialType.LINK)

    @property
    def file_attachments(self):
        return self.attachments.filter(material_type=MaterialType.FILE)

    @property
    def video_attachments(self):
        return self.attachments.filter(material_type=MaterialType.VIDEO)

    def material_presence(self):
        """返回本版本实际提交了哪些材料类型（用于校验必填项）。

        文字说明单独判断（它存在 text_content 里，不是附件）。
        """
        present = set()
        if (self.text_content or "").strip():
            present.add(MaterialType.TEXT)
        for row in self.attachments.values("material_type").distinct():
            present.add(row["material_type"])
        return present


class Attachment(models.Model):
    """一次提交中的一份材料。

    **必须挂 SubmissionVersion**（不是 Submission）—— 这是"旧版本材料不被覆盖"
    能成立的前提。重提时新版本带自己的附件，旧版本的附件原样保留。

    图片类材料会存两条记录：主图（is_thumbnail=False）与缩略图（is_thumbnail=True），
    并用 parent_attachment 互相关联。配额统计时**按主图记录汇总一次**，
    但其字节数包含缩略图（见 size_bytes + thumbnail_bytes）。
    """

    version = models.ForeignKey(
        SubmissionVersion,
        verbose_name="所属提交版本",
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    material_type = models.CharField(
        "材料类型", max_length=20, choices=MaterialType.choices, db_index=True,
    )

    # --- 存储标识（服务端生成的 uuid，绝不采用用户提供的路径）---
    storage_key = models.CharField(
        "存储对象键", max_length=255, unique=True,
        help_text="形如 <uuid>.jpg；由服务端生成，不含任何用户输入",
    )

    # --- 元数据 ---
    original_name = models.CharField(
        "原始文件名", max_length=255, blank=True,
        help_text="仅作为展示元数据保存，不参与任何路径拼接",
    )
    detected_content_type = models.CharField(
        "实际检测出的类型", max_length=80, blank=True,
        help_text="依据文件内容（魔数/解码）判定，不采信客户端声明",
    )
    size_bytes = models.BigIntegerField(
        "字节数", default=0, help_text="该对象在存储中的真实大小",
    )

    # --- 图片特有 ---
    is_thumbnail = models.BooleanField(
        "是否为缩略图", default=False,
        help_text="缩略图仅用于列表与预览，不能冒充原尺寸主图",
    )
    parent_attachment = models.ForeignKey(
        "self",
        verbose_name="对应主图",
        on_delete=models.CASCADE, null=True, blank=True,
        related_name="thumbnails",
        help_text="缩略图指向它的主图附件；主图为空",
    )
    thumbnail_bytes = models.BigIntegerField(
        "缩略图字节数", default=0,
        help_text="仅主图记录填写，便于配额统计一次算全（主图+缩略图）",
    )

    # --- 链接类材料 ---
    external_url = models.URLField(
        "外部链接", max_length=500, blank=True,
        help_text="仅允许 http/https；服务端不会主动访问该地址",
    )

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="上传者",
        on_delete=models.PROTECT,
        related_name="uploaded_attachments",
    )
    uploaded_at = models.DateTimeField("上传时间", default=timezone.now)

    class Meta:
        verbose_name = "附件"
        verbose_name_plural = "附件"
        ordering = ["id"]
        constraints = [
            # 链接类材料必须有 URL；非链接类必须有存储对象
            models.CheckConstraint(
                condition=(
                    Q(material_type="link", external_url__gt="")
                    | (~Q(material_type="link") & ~Q(storage_key=""))
                ),
                name="attachment_link_or_object",
            ),
            # 缩略图必须指向主图；主图不应有 parent
            models.CheckConstraint(
                condition=Q(is_thumbnail=False) | Q(parent_attachment__isnull=False),
                name="thumbnail_requires_parent",
            ),
        ]
        indexes = [
            models.Index(fields=["version", "material_type"]),
            models.Index(fields=["material_type"]),
        ]

    def __str__(self):
        return "%s（%s）" % (self.original_name or self.storage_key,
                             self.get_material_type_display())

    @property
    def is_image(self):
        return self.material_type == MaterialType.IMAGE

    @property
    def is_link(self):
        return self.material_type == MaterialType.LINK

    @property
    def counted_bytes(self):
        """计入图片配额的字节数。

        只对**主图记录**计数，且把它的缩略图一并算进来 ——
        这样"一次上传只算一张图片，但存储量算主图+缩略图的真实字节数"
        两个要求同时成立。
        """
        if self.is_thumbnail:
            return 0
        return self.size_bytes + self.thumbnail_bytes


class OrphanObject(models.Model):
    """待清理的孤立存储对象。

    来源：数据库写入失败后回删对象失败（网络抖动、权限问题等）。
    P3 明确要求："若对象清理失败，记录可追溯的待清理状态，不可假装已经清理"。
    本表就是那个可追溯状态 —— 运维/管理命令可据此重试删除，
    而不是让对象永久留在存储里却无人知晓。

    同时它让"清理失败"这件事**可见**：verify_stage3 会检查是否有长期未处理的
    孤立对象；管理端页面也会显示数量。
    """

    storage_key = models.CharField("存储对象键", max_length=255, unique=True)
    reason = models.CharField("产生原因", max_length=200, blank=True)
    created_at = models.DateTimeField("产生时间", auto_now_add=True)
    resolved = models.BooleanField("已清理", default=False, db_index=True)
    resolved_at = models.DateTimeField("清理时间", null=True, blank=True)

    class Meta:
        verbose_name = "待清理对象"
        verbose_name_plural = "待清理对象"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["resolved", "created_at"]),
        ]

    def __str__(self):
        return "%s（%s）" % (self.storage_key, "已清理" if self.resolved else "待清理")

    def mark_resolved(self):
        self.resolved = True
        self.resolved_at = timezone.now()
        self.save(update_fields=["resolved", "resolved_at"])


class UserImageUsage(models.Model):
    """每名用户的图片使用量（配额计数）。

    这是**冗余的加速表**，不是唯一真相来源 ——
    它必须能通过 Attachment 重新核算（见 recalculate_from_attachments）。
    之所以单独存一份：配额检查要在行级锁内快速完成，
    每次都全表扫附件在数据量大时会拖慢提交。

    并发安全：配额检查与预留统一走 `select_for_update` 锁这一行。
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        verbose_name="用户",
        on_delete=models.CASCADE,
        related_name="image_usage",
    )
    image_count = models.PositiveIntegerField("已用图片张数", default=0)
    total_bytes = models.BigIntegerField("已用图片字节数", default=0)

    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        verbose_name = "图片用量"
        verbose_name_plural = "图片用量"
        constraints = [
            models.CheckConstraint(
                condition=Q(total_bytes__gte=0), name="usage_bytes_non_negative",
            ),
        ]

    def __str__(self):
        return "%s：%d 张 / %d 字节" % (self.user, self.image_count, self.total_bytes)

    @classmethod
    def recalculate_from_attachments(cls, user):
        """依据现存附件重新核算用量（用于自检与修复）。

        只统计**主图**记录（缩略图通过 thumbnail_bytes 已包含），
        且只统计仍然存在的工作记录 —— 但注意：**历史版本的图片照样计入**，
        因为"不得通过覆盖或删除旧版本来规避配额"。
        """
        from django.db.models import Count, Sum

        agg = (
            Attachment.objects
            .filter(uploaded_by=user, material_type=MaterialType.IMAGE, is_thumbnail=False)
            .aggregate(
                count=Count("id"),
                byte_sum=Sum("size_bytes"),
                thumb_sum=Sum("thumbnail_bytes"),
            )
        )
        count = agg["count"] or 0
        total = (agg["byte_sum"] or 0) + (agg["thumb_sum"] or 0)
        usage, _ = cls.objects.get_or_create(user=user)
        usage.image_count = count
        usage.total_bytes = total
        usage.save(update_fields=["image_count", "total_bytes", "updated_at"])
        return usage
