# -*- coding: utf-8 -*-
"""提交编排：把「校验 → 上传 → 落库 → 失败补偿」串成一个可调用的整体。

调用关系：
    视图 → orchestration.submit_work() → pipeline（处理材料）+ services（锁与落库）

事务边界（关键设计）
--------------------
    事务外：材料校验、图片处理、对象上传（网络 IO，不能占着数据库锁）
    ── 开启事务 ──
        锁分配行 / 锁用量行 → 复核限制 → 写记录+版本+附件 → 配额记账
    ── 提交 ──
    失败：回删本次已上传对象；删不掉则记 OrphanObject

这样做的两个直接好处：
  * 并发提交不会因为网络慢而长时间持锁；
  * 数据库是"最后一步"，因此**不可能出现"已提交成功却没有附件"的记录**。
"""
from __future__ import annotations

from django.db import IntegrityError, transaction

from . import services
from .models import Submission, SubmissionVersion
from .pipeline import (
    cleanup_uploaded,
    prepare_file,
    prepare_image,
    prepare_link,
    record_orphans,
)
from .services import PendingUpload, SubmissionError


def _declared_material_types(post_data, files) -> set:
    """在上传**之前**，先从原始请求判断用户提交了哪些材料类型。

    这一步很关键：材料「是否被模板允许」的检查必须早于图片处理与对象上传，
    否则被拒的材料也已经白白上传了一遍（浪费存储与带宽，还多出补偿清理）。
    """
    present = set()
    if (post_data.get("text_content") or "").strip():
        present.add("text")
    if [f for f in files.getlist("images") if getattr(f, "name", "")]:
        present.add("image")
    if [f for f in files.getlist("files") if getattr(f, "name", "")]:
        present.add("file")
    if (post_data.get("link") or "").strip():
        present.add("link")
    return present


def assert_materials_allowed(task, present_types) -> None:
    """上传前的前置检查：用户提交的材料类型是否都在模板允许范围内。

    与 services.assert_material_requirements 的分工：
      * 本函数只看「类型是否允许」，纯判断、不碰文件；
      * 那个函数在上传后做「必填是否齐全」的完整校验。

    task 为 None（分配/记录不存在）时**跳过**前置检查 ——
    真正的存在性与归属校验由事务内的加锁逻辑负责，那里会给出准确错误。
    """
    from core.upload_config import MATERIAL_LABELS

    if task is None or not present_types:
        return

    snapshot = task.rule_snapshot or {}
    disallowed = []
    for key in ("text", "image", "file", "link"):
        if key in present_types and not snapshot.get("allow_%s" % key):
            disallowed.append(MATERIAL_LABELS.get(key, key))
    if disallowed:
        raise SubmissionError(
            "本任务不允许提交以下材料：%s。" % "、".join(disallowed)
        )


def _collect_material_uploads(post_data, files, *, user) -> list[PendingUpload]:
    """把表单里提交的材料整理成待上传清单。

    表单字段约定（与视图/模板一致）：
        images     多文件
        files      多文件
        video      单文件
        link       单值
    """
    uploads: list[PendingUpload] = []

    for upload in files.getlist("images"):
        if not upload or not getattr(upload, "name", ""):
            continue
        uploads.extend(prepare_image(upload, user=user))

    for upload in files.getlist("files"):
        if not upload or not getattr(upload, "name", ""):
            continue
        uploads.append(prepare_file(upload, user=user))

    link = (post_data.get("link") or "").strip()
    if link:
        uploads.append(prepare_link(link))

    return uploads


def _all_uploaded_keys(uploads: list[PendingUpload]) -> list[str]:
    keys: list[str] = []
    for item in uploads:
        keys.extend(item.uploaded_keys or [])
    return keys


def _peek_task(assignment_id: int):
    """只读地取出任务（用于上传前的纯判断，不加锁、不写库）。

    真正的一致性判断仍在事务内、加锁之后进行 ——
    这里只是为了尽早拒绝明显不被允许的材料。
    """
    from tasks.models import TaskAssignment

    row = (
        TaskAssignment.objects
        .select_related("task")
        .filter(pk=assignment_id)
        .first()
    )
    return row.task if row else None


def _peek_submission_task(submission_id: int):
    """只读地取出某记录对应的任务（上传前判断用）。"""
    from .models import Submission

    row = (
        Submission.objects
        .select_related("assignment", "assignment__task")
        .filter(pk=submission_id)
        .first()
    )
    return row.assignment.task if row and row.assignment_id else None


def _count_images(uploads: list[PendingUpload]) -> tuple[int, int]:
    """统计本次新增的图片张数与字节数。

    张数：只数主图记录（缩略图不算独立一张）；
    字节数：主图 + 缩略图的真实字节数之和。
    """
    count = 0
    total = 0
    for item in uploads:
        if item.material_type != "image" or item.is_thumbnail:
            continue
        count += 1
        total += item.size_bytes + item.thumbnail_bytes
    return count, total


def _assert_request_limits(post_data, files, limits):
    """在上传前限制数量、文字长度和请求总字节数。"""
    text = post_data.get("text_content") or ""
    if len(text) > limits.text_max_chars:
        raise SubmissionError(
            "文字说明不能超过 %d 个字符。" % limits.text_max_chars,
        )

    images = [f for f in files.getlist("images") if getattr(f, "name", "")]
    ordinary_files = [f for f in files.getlist("files") if getattr(f, "name", "")]
    if len(images) > limits.images_per_submission:
        raise SubmissionError(
            "一次最多提交 %d 张图片，当前为 %d 张。"
            % (limits.images_per_submission, len(images))
        )
    if len(ordinary_files) > limits.files_per_submission:
        raise SubmissionError(
            "一次最多提交 %d 个文件，当前为 %d 个。"
            % (limits.files_per_submission, len(ordinary_files))
        )
    total_size = sum(
        int(getattr(item, "size", 0) or 0)
        for item in images + ordinary_files
    )
    if total_size > limits.submission_total_bytes:
        from core.upload_config import human_size

        raise SubmissionError(
            "本次上传总大小不能超过 %s，当前为 %s。"
            % (human_size(limits.submission_total_bytes), human_size(total_size))
        )


def submit_new(*, assignment_id: int, user, text_content: str,
               post_data, files) -> Submission:
    """新建一条工作记录（首次提交）。

    成功后返回 Submission（状态为待审核）。
    """
    limits = services.get_limits()
    _assert_request_limits(post_data, files, limits)

    # --- 上传前的前置检查（材料类型是否被允许）---
    # 必须在图片处理与对象上传之前 —— 否则被拒的材料也已白白上传一遍。
    # 这里不持锁、不碰数据库，只做纯判断。
    assignment_task = _peek_task(assignment_id)
    assert_materials_allowed(assignment_task, _declared_material_types(post_data, files))

    # --- 事务外：准备材料（含图片处理与存储上传）---
    uploads = _collect_material_uploads(post_data, files, user=user)
    uploaded_keys = _all_uploaded_keys(uploads)

    try:
        with transaction.atomic():
            # 锁住分配行 —— 提交次数判断的并发安全依赖它
            assignment = services.lock_assignment(assignment_id)
            services.assert_can_create_submission(assignment, user)

            task = assignment.task
            services.assert_material_requirements(task, uploads, text_content)

            new_count, new_bytes = _count_images(uploads)
            if new_count:
                usage = services.lock_image_usage(user)
                services.assert_image_quota(
                    usage, new_count=new_count, new_bytes=new_bytes, limits=limits,
                )
                quota_usage = usage
            else:
                quota_usage = None

            submission, _version = services.create_submission_with_version(
                assignment=assignment, user=user,
                text_content=text_content, pending_uploads=uploads, limits=limits,
            )

            if quota_usage is not None:
                services.apply_image_usage(
                    quota_usage, new_count=new_count, new_bytes=new_bytes,
                )

            return submission
    except (SubmissionError, IntegrityError):
        # 数据库未写入成功 → 回删本次已上传的对象
        _compensate(uploaded_keys, reason="提交失败回滚")
        raise
    except Exception:
        _compensate(uploaded_keys, reason="提交异常回滚")
        raise


def submit_revision(*, submission_id: int, user, text_content: str,
                    post_data, files) -> SubmissionVersion:
    """退回后修改并重新提交。

    **不新建 Submission** —— 因此不额外占用新增记录次数（规则 6）。
    旧版本与其附件保持不动，新版本独立记录。
    """
    limits = services.get_limits()
    _assert_request_limits(post_data, files, limits)

    # 上传前的前置检查（材料类型是否被允许）—— 见 submit_new 中的说明
    submission_for_task = _peek_submission_task(submission_id)
    assert_materials_allowed(
        submission_for_task, _declared_material_types(post_data, files))

    uploads = _collect_material_uploads(post_data, files, user=user)
    uploaded_keys = _all_uploaded_keys(uploads)

    try:
        with transaction.atomic():
            # 锁记录行 —— 防止并发重复重提
            submission = services.lock_submission(submission_id)

            if submission.assignment.member_id != user.id:
                raise SubmissionError("这不是您的工作记录，无法修改。")
            if not submission.assignment.is_active:
                raise SubmissionError("该任务分配已被取消，不能继续修改。")
            from core.permissions import user_is_member
            if not user_is_member(user):
                raise SubmissionError("您当前没有干事权限，不能继续修改。")
            if not submission.can_be_revised:
                raise SubmissionError(
                    "当前状态为「%s」，只有「退回修改」的记录才能重新提交。"
                    % submission.get_status_display()
                )

            # 归档比赛不允许新增工作记录，但**允许**在既有记录上修改重提
            # （规则 13：已存在的记录可继续审核或修改）
            task = submission.assignment.task
            services.assert_material_requirements(task, uploads, text_content)

            new_count, new_bytes = _count_images(uploads)
            if new_count:
                usage = services.lock_image_usage(user)
                services.assert_image_quota(
                    usage, new_count=new_count, new_bytes=new_bytes, limits=limits,
                )
                quota_usage = usage
            else:
                quota_usage = None

            version = services.add_version(
                submission=submission, user=user,
                text_content=text_content, pending_uploads=uploads, limits=limits,
            )

            if quota_usage is not None:
                services.apply_image_usage(
                    quota_usage, new_count=new_count, new_bytes=new_bytes,
                )

            return version
    except (SubmissionError, IntegrityError):
        _compensate(uploaded_keys, reason="重新提交失败回滚")
        raise
    except Exception:
        _compensate(uploaded_keys, reason="重新提交异常回滚")
        raise


def _compensate(uploaded_keys: list[str], *, reason: str) -> None:
    """失败补偿：回删对象；删不掉的记入待清理，不假装已清理。"""
    if not uploaded_keys:
        return
    _deleted, failed = cleanup_uploaded(uploaded_keys)
    if failed:
        record_orphans(failed, reason=reason)
