"""模板上下文处理器。

两个职责：
  · site_context  —— 全站通用的站点信息（原有的）
  · welcome_tour  —— 登录后弹一次的使用说明（按身份给内容）
"""
from __future__ import annotations

from django.conf import settings


def site_context(request):
    """全站通用：站点名称与 DEBUG 开关。"""
    return {
        "SITE_NAME": getattr(settings, "SITE_NAME", "乒乓球社工作统计"),
        "DEBUG": settings.DEBUG,
    }


def welcome_tour(request):
    """登录后弹一次的使用说明（读取即消费，保证只弹一次）。

    为什么用 context processor 而不是中间件：
        这是纯粹的展示状态，只在渲染模板时需要。中间件会对每个请求
        （含静态资源、健康检查）都跑一遍，没必要。

    为什么不能直接在模板里读 session：
        Django 模板无法修改 session；而"只弹一次"要求读取的同时清掉标记。
    """
    if not getattr(request, "session", None):
        return {}

    # pop：读取的同时清掉，保证"只弹一次"
    if not request.session.pop("show_welcome", False):
        return {}

    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    from core.permissions import user_is_admin

    is_admin = user_is_admin(user)

    # 是否被授权审核某场比赛（决定要不要多讲一句审核的事）
    review_competitions = []
    if not is_admin:
        from competitions.permissions import competitions_reviewable_by

        review_competitions = list(
            competitions_reviewable_by(user).values_list("name", flat=True)[:3]
        )

    if is_admin:
        title = "欢迎使用，管理员"
        items = [
            "在「比赛」里创建比赛，并把它设为「进行中」。",
            "在比赛详情里从模板添加任务，再指定由谁来做。",
            "「任务模板」定义一类工作要交哪些材料，可重复使用。",
            "「审核」用来处理成员提交的成果；「统计报表」可导出 Excel。",
        ]
    elif review_competitions:
        names = "、".join(review_competitions)
        title = "欢迎使用"
        items = [
            "「我的任务」里只有分配给你的任务，做完点「提交成果」即可。",
            "提交后可在「我的工作记录」查看审核结果与退回意见。",
            "你还需要审核：%s。相关提交会出现在「审核」里。" % names,
        ]
    else:
        title = "欢迎使用"
        items = [
            "「我的任务」里只有分配给你的任务，做完点「提交成果」即可。",
            "「我的工作记录」可以查看自己提交过的内容与审核结果。",
            "「比赛」可以了解社团正在办哪些比赛。",
        ]

    return {
        "welcome_tour": {
            "title": title,
            "items": items,
            "name": user.get_short_name(),
        }
    }
