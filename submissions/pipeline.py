# -*- coding: utf-8 -*-
"""上传管线：校验 → 处理 → 上传存储 → 落库（失败补偿）。

流程与各步骤的归属
------------------
    1. 校验单文件大小 / 类型          事务外（纯逻辑）
    2. 图片：预处理并算真实字节        事务外（core.imaging，纯逻辑）
    3. 上传主图与缩略图到私有存储      事务外（网络 IO，不能占着数据库事务）
    4. 写数据库（附件 + 记录 + 版本）  事务内，含行级锁
    5. 写库失败 → 回删本次已上传的对象  补偿

为什么第 3 步必须在事务外：
    COS 上传是网络操作，可能耗时数秒。若放在数据库事务里，
    事务会一直持有行锁，并发提交全被堵住，还会因超时回滚业务数据。
    因此采用「先上传对象、后写库、失败删对象」的顺序。

失败补偿的三条硬要求（P3 明确）
    1. 若数据库写入失败，尝试删除本次上传形成的对象；
    2. 若对象清理失败，记录可追溯的待清理状态，**不可假装已经清理**；
    3. 若上传中途失败，不得产生"已提交成功"的工作记录。

第 1、3 条通过"数据库写入是最后一步且在同一事务内"天然满足；
第 2 条由 OrphanObject 记录实现（见 submissions/models.py 的说明与下方
record_orphans）。无数据库写入则无记录，因此不会出现"半个提交"。
"""
from __future__ import annotations

import os

from django.core.files.uploadedfile import UploadedFile

from core.imaging import ImageValidationError, process_image
from core.storage import generate_object_key, get_storage
from core.upload_config import (
    ALLOWED_FILE_EXTENSIONS,
    ALLOWED_VIDEO_EXTENSIONS,
    get_upload_limits,
    human_size,
)

from .models import MaterialType
from .services import PendingUpload, SubmissionError

# 文件头魔数（用于非图片类的真实类型判断）
_FILE_MAGICS = {
    b"%PDF": "application/pdf",
    b"PK\x03\x04": "application/zip",      # docx/xlsx/pptx/zip 共用
    b"\xd0\xcf\x11\xe0": "application/msword",   # 旧版 Office
    b"7z\xbc\xaf\x27\x1c": "application/x-7z-compressed",
}

_VIDEO_MAGICS = {
    b"\x00\x00\x00\x18ftyp": "video/mp4",   # 常见偏移处
    b"\x00\x00\x00\x20ftyp": "video/mp4",
    b"\x1a\x45\xdf\xa3": "video/webm",
}


def _head_bytes(upload: UploadedFile, size: int = 32) -> bytes:
    """读取文件开头若干字节（读取后把指针复位，避免影响后续处理）。"""
    pos = upload.tell()
    upload.seek(0)
    head = upload.read(size)
    upload.seek(pos)
    return head or b""


def _detect_binary_kind(head: bytes) -> str | None:
    """依据文件头判断非图片/视频类文件的真实类型。"""
    for magic, kind in _FILE_MAGICS.items():
        if head.startswith(magic):
            return kind
    return None


def _detect_video_kind(head: bytes) -> str | None:
    """判断视频真实类型。

    mp4 的 'ftyp' box 不一定在第 0 字节，在头部窗口内搜索更稳。
    """
    if b"ftyp" in head[:32]:
        return "video/mp4"
    for magic, kind in _VIDEO_MAGICS.items():
        if head.startswith(magic):
            return kind
    return None


def _safe_original_name(upload: UploadedFile) -> str:
    """把原始文件名处理成**仅用于展示**的安全元数据。

    去掉任何路径成分（防目录穿越），并限长。
    注意：这个名字**不会**参与存储对象键的生成。
    """
    raw = getattr(upload, "name", "") or ""
    # 同时处理 Windows 与 POSIX 分隔符
    base = raw.replace("\\", "/").split("/")[-1]
    base = base.strip().strip(".")
    if not base:
        base = "未命名文件"
    return base[:200]


def _validate_size(upload: UploadedFile, material_type: str, limits) -> int:
    """校验单文件大小上限，返回实际字节数。"""
    size = getattr(upload, "size", None)
    if size is None:
        upload.seek(0, os.SEEK_END)
        size = upload.tell()
        upload.seek(0)

    max_bytes = limits.max_bytes_for(material_type)
    if size > max_bytes:
        raise SubmissionError(
            "单个文件不能超过 %s，当前文件为 %s。"
            % (human_size(max_bytes), human_size(size))
        )
    if size == 0:
        raise SubmissionError("文件内容为空，请重新选择。")
    return size


def _check_extension(upload: UploadedFile, allowed: set[str], label: str) -> None:
    name = _safe_original_name(upload).lower()
    ext = os.path.splitext(name)[1]
    if ext not in allowed:
        raise SubmissionError(
            "%s仅支持以下格式：%s。当前文件为 %s。"
            % (label, "、".join(sorted(allowed)), ext or "无扩展名")
        )


# ---------------------------------------------------------------------------
# 图片
# ---------------------------------------------------------------------------

def prepare_image(upload: UploadedFile, *, user) -> list[PendingUpload]:
    """处理一张图片，生成「主图 + 缩略图」两份待上传材料。

    返回两条 PendingUpload：主图（含 thumbnail_bytes）与缩略图（parent_key 指向主图）。
    配额按主图记录的 counted_bytes 统计 —— 即"一次上传算一张图，
    但存储量算主图+缩略图的真实字节数"。
    """
    limits = get_upload_limits()
    _validate_size(upload, MaterialType.IMAGE, limits)

    # 图片的格式判断交由 imaging 模块用「魔数 + 真实解码」完成，
    # 这里刻意不看扩展名与 Content-Type —— 它们都由客户端控制。
    data = upload.read()
    if not isinstance(data, bytes):
        data = bytes(data)
    try:
        result = process_image(
            data,
            max_edge=limits.image_max_edge,
            image_quality=limits.image_quality,
            thumb_edge=limits.thumb_edge,
            thumb_quality=limits.thumb_quality,
        )
    except ImageValidationError as exc:
        raise SubmissionError(str(exc)) from exc

    main_key = generate_object_key(".jpg")
    thumb_key = generate_object_key(".jpg", thumb=True)

    store = get_storage()
    # 先落存储（网络 IO，在事务外）
    store.save(main_key, result.main_bytes)
    store.save(thumb_key, result.thumb_bytes)

    original = _safe_original_name(upload)
    main = PendingUpload(
        material_type=MaterialType.IMAGE,
        storage_key=main_key,
        original_name=original,
        detected_content_type="image/jpeg",   # 处理结果统一为 JPEG
        size_bytes=len(result.main_bytes),
        thumbnail_bytes=len(result.thumb_bytes),
        uploaded_keys=[main_key, thumb_key],
    )
    thumb = PendingUpload(
        material_type=MaterialType.IMAGE,
        storage_key=thumb_key,
        original_name="%s（缩略图）" % original,
        detected_content_type="image/jpeg",
        size_bytes=len(result.thumb_bytes),
        is_thumbnail=True,
        parent_key=main_key,
        uploaded_keys=[],        # 与主图共用一次上传的补偿清单，避免重复删除
    )
    return [main, thumb]


# ---------------------------------------------------------------------------
# 普通文件 / 视频
# ---------------------------------------------------------------------------

def prepare_file(upload: UploadedFile, *, user) -> PendingUpload:
    """处理一个普通文件附件。

    大文件**不整读进内存**：先用文件头校验真实类型，再交给存储适配层的
    `save_from_path`（COS 走分块上传）；Django 的 UploadedFile 本身会把大文件
    落到临时文件而不是内存（由 FILE_UPLOAD_MAX_MEMORY_SIZE 控制）。
    """
    limits = get_upload_limits()
    size = _validate_size(upload, MaterialType.FILE, limits)
    _check_extension(upload, ALLOWED_FILE_EXTENSIONS, "文件附件")

    head = _head_bytes(upload)
    kind = _detect_binary_kind(head)
    if kind is None:
        raise SubmissionError(
            "无法识别文件内容类型，仅支持 PDF、Office 文档、压缩包与纯文本。"
        )

    ext = os.path.splitext(_safe_original_name(upload))[1].lower() or ".bin"
    key = generate_object_key(ext)
    _store_upload(upload, key)

    return PendingUpload(
        material_type=MaterialType.FILE,
        storage_key=key,
        original_name=_safe_original_name(upload),
        detected_content_type=kind,
        size_bytes=size,
        uploaded_keys=[key],
    )


def prepare_video(upload: UploadedFile, *, user) -> PendingUpload:
    """处理一个视频附件。"""
    limits = get_upload_limits()
    size = _validate_size(upload, MaterialType.VIDEO, limits)
    _check_extension(upload, ALLOWED_VIDEO_EXTENSIONS, "视频")

    head = _head_bytes(upload, 64)
    kind = _detect_video_kind(head)
    if kind is None:
        raise SubmissionError("无法识别视频格式，仅支持 MP4、MOV、WebM。")

    ext = os.path.splitext(_safe_original_name(upload))[1].lower() or ".mp4"
    key = generate_object_key(ext)
    _store_upload(upload, key)

    return PendingUpload(
        material_type=MaterialType.VIDEO,
        storage_key=key,
        original_name=_safe_original_name(upload),
        detected_content_type=kind,
        size_bytes=size,
        uploaded_keys=[key],
    )


def _store_upload(upload: UploadedFile, key: str) -> None:
    """把上传内容写入存储，尽量走"从临时文件复制"的路径避免整读内存。"""
    store = get_storage()
    temp_path = getattr(upload, "temporary_file_path", None)
    if callable(temp_path):
        # Django 对大文件会自动落到临时文件，这里直接复制（COS 走分块上传）
        store.save_from_path(key, temp_path())
    else:
        upload.seek(0)
        store.save(key, upload.read())


# ---------------------------------------------------------------------------
# 链接（不落存储，也不主动访问）
# ---------------------------------------------------------------------------

def prepare_link(url: str) -> PendingUpload:
    """校验并准备一个作品链接。

    只允许 http / https；**服务端不会主动访问该地址**
    （避免 SSRF：用户给出内网地址诱导服务器请求）。
    """
    from urllib.parse import urlparse

    value = (url or "").strip()
    if not value:
        raise SubmissionError("请填写有效的作品链接。")

    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise SubmissionError("作品链接必须以 http:// 或 https:// 开头。")
    if not parsed.netloc:
        raise SubmissionError("作品链接不完整，请检查后重试。")

    return PendingUpload(
        material_type=MaterialType.LINK,
        external_url=value[:500],
        original_name=value[:200],
        detected_content_type="text/uri",
        size_bytes=0,       # 链接不占存储
    )


# ---------------------------------------------------------------------------
# 失败补偿
# ---------------------------------------------------------------------------

def cleanup_uploaded(uploaded_keys) -> tuple[list[str], list[str]]:
    """回删本次上传的对象。

    返回 (已删除, 删除失败)。**删除失败的要进入待清理记录，不能假装已清理。**
    """
    store = get_storage()
    deleted, failed = [], []
    for key in uploaded_keys:
        if not key:
            continue
        try:
            if store.delete(key):
                deleted.append(key)
            else:
                # 对象本就不存在（例如根本没上传成功）—— 视为已清理
                deleted.append(key)
        except Exception:  # noqa: BLE001
            failed.append(key)
    return deleted, failed


def record_orphans(failed_keys, *, reason: str) -> int:
    """把清理失败的对象记成"待清理"，供后续核对与重试。

    这是 P3 第 7 条要求（"记录可追溯的待清理状态，不可假装已经清理"）的实现。
    """
    from .models import OrphanObject

    count = 0
    for key in failed_keys:
        OrphanObject.objects.get_or_create(
            storage_key=key,
            defaults={"reason": reason[:200], "resolved": False},
        )
        count += 1
    return count
