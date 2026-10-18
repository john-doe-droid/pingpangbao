"""
比赛层面的对象权限。

三条互不推导的分层，是本阶段安全设计的核心：

    管理权（manage）  ← 社团管理员角色
    审核权（review）  ← 只能来自 ReviewerAssignment 显式授权，不继承管理权
    查看权（view）    ← 非草稿比赛对已登录用户可见；草稿仅管理员可见

「全局查看权限不能自动推导出审核权限」这句话在这里被具体化：
user_can_view_all() 为真**不代表** can_review_competition() 为真，
后者必须查 ReviewerAssignment 表。
"""
from core.permissions import ROLE_ADMIN, user_is_admin

from .models import Competition, CompetitionStatus, ReviewerAssignment


# ---------------------------------------------------------------------------
# 查看
# ---------------------------------------------------------------------------

def can_view_competition(user, competition):
    """能否查看某场比赛。

    - 草稿：仅管理员
    - 进行中 / 已归档：所有已登录用户
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if competition.status == CompetitionStatus.DRAFT:
        return user_is_admin(user)
    return True


def visible_competitions_for(user):
    """某用户可见的比赛查询集。"""
    if not user or not getattr(user, "is_authenticated", False):
        return Competition.objects.none()
    if user_is_admin(user):
        return Competition.objects.all()
    return Competition.objects.visible_to_members()


# ---------------------------------------------------------------------------
# 管理（比赛本身的增删改与归档）
# ---------------------------------------------------------------------------

def can_manage_competition(user, competition=None):
    """能否管理比赛（创建/修改/归档/配置任务/授权审核人）。

    本阶段管理权只给社团管理员；未对单场比赛做更细的分权，
    以保持权限模型可解释（后续如需"某场比赛的负责人"再扩展）。
    """
    return user_is_admin(user)


def can_manage_competition_tasks(user, competition):
    """能否配置该比赛的任务与人员分配。

    额外要求比赛处于「进行中」——草稿尚未配置，归档后停止新增。
    """
    if not can_manage_competition(user, competition):
        return False
    return competition.status == CompetitionStatus.ACTIVE


# ---------------------------------------------------------------------------
# 审核
# ---------------------------------------------------------------------------

def can_review_competition(user, competition):
    """能否审核某场比赛的工作成果。

    **只能**来自 ReviewerAssignment 的有效授权。
    管理员身份、全局查看权限都不在此处生效 —— 这是刻意的：
    审核是有责任的动作，必须逐场显式授权。
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return ReviewerAssignment.objects.filter(
        competition=competition, reviewer=user, is_active=True,
    ).exists()


def reviewer_assignments_for(competition):
    """某场比赛当前有效的审核人授权。"""
    return competition.reviewer_assignments.filter(is_active=True).select_related("reviewer")


def can_manage_reviewers(user, competition):
    """能否为该比赛增删审核人授权。

    与审核权分离：管理员负责授权，被授权者负责审核。
    管理员自己若不在此表里，同样不能审核。
    """
    return can_manage_competition(user, competition)


def competitions_reviewable_by(user):
    """某用户被授权审核的比赛（供"待我审核"类页面使用，P3 起会用到）。"""
    if not user or not getattr(user, "is_authenticated", False):
        return Competition.objects.none()
    return Competition.objects.filter(
        reviewer_assignments__reviewer=user,
        reviewer_assignments__is_active=True,
    ).distinct()
