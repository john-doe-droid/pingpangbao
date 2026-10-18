"""
任务与分配层面的对象权限。

干事端最关键的一条约束：**只能看到分配给自己的任务**，
并且这个限制必须在后端查询层面生效，不能只靠隐藏导航或模板条件。
"""
from core.permissions import user_is_admin

from .models import CompetitionTask, TaskAssignment


# ---------------------------------------------------------------------------
# 干事端：我的任务
# ---------------------------------------------------------------------------

def assignments_for_member(user):
    """某干事本人的有效任务分配。

    这是干事端所有查询的**唯一入口** —— 任何"我的任务"页面都必须
    从这里取数据，从源头保证不会漏出别人的任务。
    """
    if not user or not getattr(user, "is_authenticated", False):
        return TaskAssignment.objects.none()
    return (
        TaskAssignment.objects
        .filter(member=user, is_active=True)
        .select_related(
            "task", "task__competition", "task__work_type",
            "task__template",
        )
        .order_by("task__competition__event_date", "task_id")
    )


def can_view_assignment(user, assignment):
    """能否查看某条分配（即某个任务在某人名下的入口）。"""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if assignment.member_id == user.id:
        return True
    # 管理员可以查看（用于配置页面），但这属于管理视角，不是干事视角
    return user_is_admin(user)


def can_view_task_as_member(user, task):
    """干事视角：该任务是否分配给了自己。"""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return TaskAssignment.objects.filter(
        task=task, member=user, is_active=True,
    ).exists()


def member_visible_tasks(user):
    """某干事可见的任务查询集（供任务详情页做对象级校验）。"""
    if not user or not getattr(user, "is_authenticated", False):
        return CompetitionTask.objects.none()
    return (
        CompetitionTask.objects
        .filter(assignments__member=user, assignments__is_active=True)
        .select_related("competition", "work_type")
        .distinct()
    )


# ---------------------------------------------------------------------------
# 管理端：任务与分配的配置权限
# ---------------------------------------------------------------------------

def can_configure_tasks(user, competition):
    """能否在该比赛下新增/修改任务。"""
    from competitions.permissions import can_manage_competition_tasks
    return can_manage_competition_tasks(user, competition)


def can_assign_task(user, task):
    """能否为某任务分配人员。

    要求：是管理员，且所属比赛处于「进行中」。
    """
    return can_configure_tasks(user, task.competition)


def can_deactivate_assignment(user, assignment):
    """能否停用某条分配。"""
    return can_assign_task(user, assignment.task)
