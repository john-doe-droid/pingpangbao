# -*- coding: utf-8 -*-
"""提交与配额的服务层。

为什么必须有服务层（而不是写在视图里）
--------------------------------------
P3 有两条规则**必须**在数据库事务 + 行级锁内完成，否则并发下必然出错：

  1. 「每人每任务新增记录数受上限限制」—— 两个人同时点提交，
     普通的「先查数量再写入」会让两边都读到"还差一个"，双双写入超额。
  2. 「图片配额 50 张 / 100 MiB」—— 同样会被并发上传绕过。

因此：
  * 提交次数：`select_for_update` 锁住 TaskAssignment 行；
  * 图片配额：`select_for_update` 锁住该用户的 UserImageUsage 行。

锁的是「被争用的那一行」，而不是整表，因此不同用户/不同任务之间不互相阻塞。

COS 与数据库不在同一事务内，所以上传流程单独处理失败补偿：
    先上传对象 → 再写数据库 → 写库失败则回删对象（见 submissions/pipeline.py）
本模块只负责「校验 + 落库」这部分，且全部在调用方的事务内执行。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from competitions.models import CompetitionStatus
from tasks.models import TaskAssignment

from .models import (
    Attachment,
    MaterialType,
    Submission,
    SubmissionStatus,
    SubmissionVersion,
    UserImageUsage,
)


class SubmissionError(Exception):
    """提交/审核过程中的业务错误。

    message 面向最终用户，可直接展示在页面上 —— 调用方不要把它变成 500。
    """


def get_limits():
    """读取当前上传与配额上限。

    包一层函数是为了让服务层有一个稳定的入口，
    同时避免在各处重复 import core.upload_config。
    """
    from core.upload_config import get_upload_limits

    return get_upload_limits()


class QuotaExceeded(SubmissionError):
    """图片配额超限。携带当前用量与上限，便于页面显示具体数字。"""

    def __init__(self, message, *, used_count, max_count, used_bytes, max_bytes):
        super().__init__(message)
        self.used_count = used_count
        self.max_count = max_count
        self.used_bytes = used_bytes
        self.max_bytes = max_bytes


@dataclass
class PendingUpload:
    """已经处理好、等待写库的一份材料。

    由上传管线（pipeline）在事务外准备，随后在事务内落库。
    """

    material_type: str
    storage_key: str = ""
    original_name: str = ""
    detected_content_type: str = ""
    size_bytes: int = 0
    is_thumbnail: bool = False
    parent_key: str = ""          # 缩略图指向的主图 storage_key
    thumbnail_bytes: int = 0      # 仅主图记录填写
    external_url: str = ""
    # 供失败补偿使用：本次上传真正写进存储的对象键
    uploaded_keys: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# 提交次数
# ---------------------------------------------------------------------------

def lock_assignment(assignment_id: int) -> TaskAssignment:
    """行级锁读取任务分配。

    必须在事务内调用。锁住这一行的效果：同一分配的并发提交会串行化，
    从而让「新增记录数」的判断可靠。
    """
    return (
        TaskAssignment.objects
        .select_for_update()
        .select_related("task", "task__competition", "member")
        .get(pk=assignment_id)
    )


def count_new_records(assignment: TaskAssignment) -> int:
    """该分配下已创建的**工作记录数**（不是版本数）。

    退回后重新提交不会新增 Submission 行，因此不占额度 —— 这是规则 6 的实现依据。
    """
    return Submission.objects.filter(assignment=assignment).count()


def assert_can_create_submission(assignment: TaskAssignment, user) -> None:
    """校验能否新建一条工作记录。**

    调用前提：已在本事务内 lock_assignment 锁住该分配。
    """
    if assignment.member_id != user.id:
        raise SubmissionError("该任务没有分配给您，无法提交。")

    if not assignment.is_active:
        raise SubmissionError("该任务分配已被取消，无法提交。")

    competition = assignment.task.competition
    if competition.status == CompetitionStatus.ARCHIVED:
        raise SubmissionError(
            "该比赛已归档，不能新增工作记录。"
            "（已存在的记录仍可继续审核或修改）"
        )

    limit = assignment.task.max_records_per_member
    used = count_new_records(assignment)
    if used >= limit:
        raise SubmissionError(
            "本任务每人最多提交 %d 条工作记录，您已提交 %d 条。"
            "如需修改，请在已有记录上按审核意见重新提交。" % (limit, used)
        )


# ---------------------------------------------------------------------------
# 图片配额
# ---------------------------------------------------------------------------

def lock_image_usage(user) -> UserImageUsage:
    """行级锁读取（必要时创建）用户的图片用量行。

    必须在事务内调用 —— 这是图片配额并发安全的唯一依据。
    """
    usage, _ = UserImageUsage.objects.get_or_create(user=user)
    # 重新以行级锁取回；get_or_create 本身不加锁
    return UserImageUsage.objects.select_for_update().get(pk=usage.pk)


def assert_image_quota(usage: UserImageUsage, *, new_count: int, new_bytes: int,
                       limits) -> None:
    """校验新增图片是否超配额。

    参数是**处理后**的主图与缩略图真实字节数之和（由 pipeline 算好），
    不是原始上传文件的字节数。
    """
    if usage.image_count + new_count > limits.images_per_user:
        raise QuotaExceeded(
            "图片数量已达上限：最多 %d 张，当前已用 %d 张，本次需要 %d 张。"
            "请删除不需要的图片后再试。"
            % (limits.images_per_user, usage.image_count, new_count),
            used_count=usage.image_count, max_count=limits.images_per_user,
            used_bytes=usage.total_bytes, max_bytes=limits.image_bytes_per_user,
        )
    if usage.total_bytes + new_bytes > limits.image_bytes_per_user:
        from core.upload_config import human_size

        raise QuotaExceeded(
            "图片存储量已达上限：最多 %s，当前已用 %s，本次需要 %s。"
            "请删除不需要的图片后再试。"
            % (human_size(limits.image_bytes_per_user),
               human_size(usage.total_bytes), human_size(new_bytes)),
            used_count=usage.image_count, max_count=limits.images_per_user,
            used_bytes=usage.total_bytes, max_bytes=limits.image_bytes_per_user,
        )


def apply_image_usage(usage: UserImageUsage, *, new_count: int, new_bytes: int) -> None:
    """配额记账（在事务内、与附件落库一起提交）。"""
    usage.image_count += new_count
    usage.total_bytes += new_bytes
    usage.save(update_fields=["image_count", "total_bytes", "updated_at"])


# ---------------------------------------------------------------------------
# 版本分配
# ---------------------------------------------------------------------------

def allocate_version_no(submission: Submission) -> int:
    """分配下一个版本号。

    规则：同一记录内从 1 开始**连续**递增。
    在调用方已持有该分配行锁的前提下，这里再取一次当前最大值即可 ——
    因为同一分配的并发提交已被行锁串行化。

    另有数据库唯一约束 `uniq_version_no_per_submission` 作为最后一道防线：
    万一有人绕过服务层，重复版本号会被数据库拒绝。
    """
    current_max = submission.versions.aggregate(m=Max("version_no"))["m"] or 0
    return current_max + 1


# ---------------------------------------------------------------------------
# 创建记录与版本（在调用方事务内执行）
# ---------------------------------------------------------------------------

def create_submission_with_version(
    *,
    assignment: TaskAssignment,
    user,
    text_content: str,
    pending_uploads: list[PendingUpload],
    limits,
) -> tuple[Submission, SubmissionVersion]:
    """新建一条工作记录 + 它的第 1 版。

    调用前提（由调用方保证）：
      * 已开启事务；
      * 已 lock_assignment 并完成 assert_can_create_submission；
      * 已对 pending_uploads 完成存储层上传（失败补偿由调用方负责）。
    """
    now = timezone.now()
    deadline = assignment.task.effective_deadline

    submission = Submission.objects.create(
        assignment=assignment,
        status=SubmissionStatus.PENDING,
    )

    version = SubmissionVersion.objects.create(
        submission=submission,
        version_no=1,
        text_content=text_content or "",
        submitted_by=user,
        submitted_at=now,
        # 逾期判定只看"提交时刻 vs 提交时生效的截止时间"，与之后是否被改无关
        effective_deadline=deadline,
        is_overdue=deadline < now,
    )

    _persist_attachments(version, pending_uploads, user)

    submission.current_version = version
    submission.save(update_fields=["current_version", "updated_at"])
    return submission, version


def add_version(
    *,
    submission: Submission,
    user,
    text_content: str,
    pending_uploads: list[PendingUpload],
    limits,
) -> SubmissionVersion:
    """在既有记录上追加一版（退回后的重新提交）。

    **不**新建 Submission —— 因此不占新增记录额度（规则 6）。
    旧版本与其附件原样保留（规则：旧版本内容与材料不可被覆盖）。
    """
    now = timezone.now()
    deadline = submission.assignment.task.effective_deadline

    version = SubmissionVersion.objects.create(
        submission=submission,
        version_no=allocate_version_no(submission),
        text_content=text_content or "",
        submitted_by=user,
        submitted_at=now,
        effective_deadline=deadline,
        is_overdue=deadline < now,
    )

    _persist_attachments(version, pending_uploads, user)

    submission.current_version = version
    submission.status = SubmissionStatus.PENDING      # 重新提交后回到待审核
    submission.save(update_fields=["current_version", "status", "updated_at"])
    return version


def _persist_attachments(version: SubmissionVersion,
                         pending_uploads: list[PendingUpload], user) -> None:
    """把准备好的材料写进 Attachment。

    主图与缩略图分两条记录，用 parent_attachment 关联；
    主图记录上写 thumbnail_bytes，使配额统计一次算全。
    """
    # 先建主图（及其它非缩略图），再建缩略图，以便建立父子关系
    main_by_key: dict[str, Attachment] = {}

    for item in pending_uploads:
        if item.is_thumbnail:
            continue
        att = Attachment.objects.create(
            version=version,
            material_type=item.material_type,
            storage_key=item.storage_key or "",
            original_name=item.original_name,
            detected_content_type=item.detected_content_type,
            size_bytes=item.size_bytes,
            thumbnail_bytes=item.thumbnail_bytes,
            external_url=item.external_url,
            uploaded_by=user,
        )
        if item.storage_key:
            main_by_key[item.storage_key] = att

    for item in pending_uploads:
        if not item.is_thumbnail:
            continue
        parent = main_by_key.get(item.parent_key)
        Attachment.objects.create(
            version=version,
            material_type=item.material_type,
            storage_key=item.storage_key,
            original_name=item.original_name,
            detected_content_type=item.detected_content_type,
            size_bytes=item.size_bytes,
            is_thumbnail=True,
            parent_attachment=parent,
            uploaded_by=user,
        )


# ---------------------------------------------------------------------------
# 材料要求校验（依据任务规则快照，不是模板当前值）
# ---------------------------------------------------------------------------

def assert_material_requirements(task, pending_uploads: list[PendingUpload],
                                text_content: str) -> None:
    """按任务的 rule_snapshot 校验必填材料。

    用快照而不是模板当前值 —— 模板后来被改不该影响已发布任务的规则（P2 设计）。
    """
    from core.upload_config import MATERIAL_LABELS

    snapshot = task.rule_snapshot or {}
    present = set()
    if (text_content or "").strip():
        present.add(MaterialType.TEXT)
    for item in pending_uploads:
        present.add(item.material_type)

    missing = []
    for key in ("text", "image", "file", "link", "video"):
        if snapshot.get("require_%s" % key) and key not in present:
            missing.append(MATERIAL_LABELS.get(key, key))

    if missing:
        raise SubmissionError(
            "本次提交缺少必填材料：%s。请补齐后再提交。" % "、".join(missing)
        )

    # 反向检查：提交了模板不允许的材料
    disallowed = []
    for key in ("file", "link", "video"):
        if key in present and not snapshot.get("allow_%s" % key):
            disallowed.append(MATERIAL_LABELS.get(key, key))
    if disallowed:
        raise SubmissionError(
            "本任务不允许提交以下材料：%s。" % "、".join(disallowed)
        )


# ---------------------------------------------------------------------------
# 审核
# ---------------------------------------------------------------------------

def lock_submission(submission_id: int) -> Submission:
    """行级锁读取工作记录。审核的并发安全依赖它。"""
    return (
        Submission.objects
        .select_for_update()
        .select_related(
            "assignment", "assignment__member",
            "assignment__task", "assignment__task__competition",
        )
        .get(pk=submission_id)
    )


def approve_submission(*, submission: Submission, reviewer, comment: str = "",
                       is_reopen: bool = False, reopen_reason: str = "") -> None:
    """审核通过。

    调用前提：已在本事务内 lock_submission，且调用方已完成权限校验
    （是授权审核人、且不是提交人本人）。
    """
    if not is_reopen and submission.status != SubmissionStatus.PENDING:
        # 状态已被别人改过 —— 并发冲突，明确告知而不是静默覆盖
        raise SubmissionError(
            "这条记录的状态刚刚被他人更新为「%s」，请刷新页面后重试。"
            % submission.get_status_display()
        )
    if is_reopen and not submission.is_approved:
        raise SubmissionError("只有「审核通过」的记录才能发起重新审核。")

    version = submission.current_version or submission.latest_version()
    if version is None:
        raise SubmissionError("该记录没有任何提交版本，无法审核。")

    from_status = submission.status
    submission.status = SubmissionStatus.APPROVED
    submission.save(update_fields=["status", "updated_at"])

    _write_review_log(
        submission=submission, version=version, reviewer=reviewer,
        action="reopen" if is_reopen else "approve",
        from_status=from_status, to_status=submission.status,
        comment=comment, reopen_reason=reopen_reason,
    )


def reject_submission(*, submission: Submission, reviewer, comment: str) -> None:
    """退回修改。理由必填（数据库层也有约束兜底）。"""
    if not (comment or "").strip():
        raise SubmissionError("退回修改必须填写理由。")
    if submission.status != SubmissionStatus.PENDING:
        raise SubmissionError(
            "这条记录的状态刚刚被他人更新为「%s」，请刷新页面后重试。"
            % submission.get_status_display()
        )

    version = submission.current_version or submission.latest_version()
    if version is None:
        raise SubmissionError("该记录没有任何提交版本，无法审核。")

    from_status = submission.status
    submission.status = SubmissionStatus.NEEDS_REVISION
    submission.save(update_fields=["status", "updated_at"])

    _write_review_log(
        submission=submission, version=version, reviewer=reviewer,
        action="reject", from_status=from_status, to_status=submission.status,
        comment=comment,
    )


def _write_review_log(*, submission, version, reviewer, action,
                      from_status, to_status, comment="", reopen_reason="") -> None:
    """写审核记录 + 同步写一条通用操作审计。"""
    from reviews.models import ReviewLog

    from core.audit import log_action_for_user

    ReviewLog.objects.create(
        submission=submission,
        version=version,
        action=action,
        from_status=from_status,
        to_status=to_status,
        comment=comment or "",
        reopen_reason=reopen_reason or "",
        reviewer=reviewer,
    )
    # 通用审计（P2 已有能力，复用而不另建体系）
    # 用 log_action_for_user 显式传入操作人 —— 因为服务层可能在没有
    # HTTP request 的场景下被调用（管理命令、测试），不能依赖 request.user
    log_action_for_user(
        reviewer, "submission.%s" % action, submission,
        detail="版本 %s；%s" % (version.version_no, comment or reopen_reason or ""),
    )
