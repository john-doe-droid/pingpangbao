# -*- coding: utf-8 -*-
"""附件访问权限。

三条必须成立且互相独立的判定（P3 要求）：
    1. 匿名用户**不得**直接访问任何附件；
    2. 跨用户访问被拒 —— 干事看不到别人工作记录里的附件；
    3. 审核人只能看**被授权比赛**里的附件。

权限模型是「从附件反查它属于哪份工作记录、哪场比赛」，
然后复用 P2 已有的权限函数，不另立一套判定逻辑。
"""
from __future__ import annotations

from core.permissions import user_is_admin


def attachment_owner(attachment):
    """附件的提交人（工作记录归属的干事）。"""
    return attachment.version.submission.assignment.member


def attachment_competition(attachment):
    return attachment.version.submission.competition


def can_view_attachment(user, attachment) -> bool:
    """能否查看（并下载）某份附件。"""
    if not user or not getattr(user, "is_authenticated", False):
        return False                                   # 匿名一律拒绝

    submission = attachment.version.submission

    # 1) 本人：可看自己的全部附件（含历史版本）
    if submission.assignment.member_id == user.id:
        return True

    # 2) 社团管理员：可查看（用于管理端记录查询）
    if user_is_admin(user):
        return True

    # 3) 该比赛的授权审核人：可查看
    from competitions.permissions import can_review_competition

    if can_review_competition(user, submission.competition):
        return True

    # 4) 其它情况一律拒绝 —— 包括"全局查看"角色：
    #    能看到比赛不代表能看到别人的工作成果。
    return False


def can_review_submission(user, submission) -> bool:
    """能否审核某条工作记录。"""
    if not user or not getattr(user, "is_authenticated", False):
        return False

    # 审核人不能审核自己的提交（P3 明确要求）
    if submission.assignment.member_id == user.id:
        return False

    from competitions.permissions import can_review_competition

    return can_review_competition(user, submission.competition)


def can_reopen_submission(user, submission) -> bool:
    """能否对已通过记录发起重新审核。

    比普通审核更严格：要求是**该比赛的授权审核人**且该记录已通过。
    管理员若未被授权该比赛，同样不能重新审核 —— 与「审核权不继承管理权」一致。
    """
    if not submission.is_approved:
        return False
    return can_review_submission(user, submission)


def can_undo_rejection(user, submission) -> bool:
    """Only the reviewer who last rejected the current version may undo it."""
    from reviews.models import ReviewAction
    from submissions.models import SubmissionStatus

    if submission.status != SubmissionStatus.NEEDS_REVISION:
        return False
    if not can_review_submission(user, submission):
        return False

    current_version = submission.current_version or submission.latest_version()
    if current_version is None:
        return False
    rejection = (
        submission.review_logs
        .filter(action=ReviewAction.REJECT)
        .order_by("-acted_at", "-id")
        .first()
    )
    return bool(
        rejection
        and rejection.reviewer_id == user.id
        and rejection.version_id == current_version.pk
    )


def visible_submissions_for(user):
    """某用户可见的工作记录集合。

    与 P2 的干事端思路一致：**在查询层面**限定范围，
    而不是查出全部再用模板条件过滤。
    """
    from submissions.models import Submission

    if not user or not getattr(user, "is_authenticated", False):
        return Submission.objects.none()

    if user_is_admin(user):
        return Submission.objects.all()

    # 本人提交的 + 自己被授权审核的比赛下的
    from competitions.permissions import competitions_reviewable_by

    reviewable = competitions_reviewable_by(user).values_list("pk", flat=True)
    return Submission.objects.filter(
        models_q_member_or_competition(user.id, list(reviewable))
    )


def models_q_member_or_competition(user_id, competition_ids):
    from django.db.models import Q

    query = Q(assignment__member_id=user_id)
    if competition_ids:
        query |= Q(assignment__task__competition_id__in=competition_ids)
    return query
