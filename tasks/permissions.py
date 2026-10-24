"""
任务与分配层面的对象权限。

干事端最关键的一条约束：**只能看到分配给自己的任务**，
并且这个限制必须在后端查询层面生效，不能只靠隐藏导航或模板条件。
"""
from core.permissions import ROLE_MEMBER

from .models import TaskAssignment


# ---------------------------------------------------------------------------
# 干事端：我的工作
# ---------------------------------------------------------------------------

def assignments_for_member(user):
    """某干事本人的有效任务分配。

    这是干事端所有查询的**唯一入口** —— 任何“我的工作”页面都必须
    从这里取数据，从源头保证不会漏出别人的任务。
    """
    if not user or not getattr(user, "is_authenticated", False):
        return TaskAssignment.objects.none()
    return (
        TaskAssignment.objects
        .filter(
            member=user,
            is_active=True,
            task__competition__status="active",
            member__roles__code=ROLE_MEMBER,
            member__roles__is_active=True,
        )
        .select_related(
            "task", "task__competition", "task__work_type",
            "task__template",
        )
        .distinct()
        .order_by("task__competition__event_date", "task_id")
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
