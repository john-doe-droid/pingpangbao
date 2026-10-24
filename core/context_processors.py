"""模板上下文处理器：站点信息与导航待办计数。"""
from __future__ import annotations

from django.conf import settings


def site_context(request):
    """全站通用：站点名称与 DEBUG 开关。"""
    return {
        "SITE_NAME": getattr(settings, "SITE_NAME", "乒乓球社工作统计"),
        "DEBUG": settings.DEBUG,
    }


def navigation_context(request):
    """提供导航可见性与待办数字。

    审核入口只对当前确实拥有有效审核授权的用户显示，避免普通成员点进
    一个永远是空队列的页面。计数仅用于导航提示，真正的权限仍由视图校验。
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    from competitions.models import ReviewerAssignment
    from core.permissions import user_is_admin
    from submissions.models import Submission, SubmissionStatus

    review_competition_ids = list(
        ReviewerAssignment.objects.filter(
            reviewer=user, is_active=True,
        ).values_list("competition_id", flat=True)
    )
    can_review = bool(review_competition_ids)

    review_pending = 0
    if can_review:
        review_pending = (
            Submission.objects
            .filter(
                status=SubmissionStatus.PENDING,
                assignment__task__competition_id__in=review_competition_ids,
            )
            .exclude(assignment__member=user)
            .count()
        )

    need_revision = Submission.objects.filter(
        assignment__member=user,
        status=SubmissionStatus.NEEDS_REVISION,
    ).count()

    return {
        "nav_is_admin": user_is_admin(user),
        "nav_can_review": can_review,
        "nav_review_pending": review_pending,
        "nav_need_revision": need_revision,
    }
