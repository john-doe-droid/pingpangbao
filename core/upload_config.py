# -*- coding: utf-8 -*-
"""上传与配额限制的集中配置。

为什么单独一个模块：任务要求「上限必须配置化，不要散落在多个视图中写死数字」。
所有上限都从 Django settings 读取（settings 再从环境变量读），
因此改上限不需要动代码，只需改 .env。

在 config/settings/base.py 里通过 `UPLOAD_LIMITS` 提供默认值，
生产可用环境变量覆盖。
"""
from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

MiB = 1024 * 1024


@dataclass(frozen=True)
class UploadLimits:
    """上传与配额的统一上限。"""

    # --- 单文件输入大小上限（作用于用户上传的原始文件）---
    image_max_bytes: int = 10 * MiB
    file_max_bytes: int = 50 * MiB

    # --- 个人图片配额（每用户全局累计，跨比赛跨任务）---
    images_per_user: int = 50
    image_bytes_per_user: int = 100 * MiB

    # --- 图片处理参数 ---
    image_max_edge: int = 1280
    image_quality: int = 82
    thumb_edge: int = 400
    thumb_quality: int = 78

    # --- 单次提交总量限制 ---
    images_per_submission: int = 20
    files_per_submission: int = 5
    submission_total_bytes: int = 100 * MiB

    # --- 文本限制 ---
    text_max_chars: int = 20000
    comment_max_chars: int = 5000

    # --- 预签名下载 URL 有效期（秒）---
    presign_expires_seconds: int = 300

    def max_bytes_for(self, material_type: str) -> int:
        """按材料类型返回单文件上限。未知类型按最严格的图片上限处理。"""
        return {
            "image": self.image_max_bytes,
            "file": self.file_max_bytes,
        }.get(material_type, self.image_max_bytes)

    def human_max_for(self, material_type: str) -> str:
        """人类可读的单文件上限，用于页面提示。"""
        return human_size(self.max_bytes_for(material_type))


def human_size(num_bytes: int) -> str:
    """把字节数格式化成便于阅读的形式（用于页面提示与错误消息）。"""
    if num_bytes >= MiB:
        value = num_bytes / MiB
        # 整数就不显示小数位，避免出现「10.0 MiB」这种冗余写法
        return ("%d MiB" % value) if float(value).is_integer() else ("%.1f MiB" % value)
    if num_bytes >= 1024:
        return "%d KiB" % (num_bytes // 1024)
    return "%d 字节" % num_bytes


def get_upload_limits() -> UploadLimits:
    """从 settings 读取上限配置，缺项时用默认值。

    `UPLOAD_LIMITS` 是一个 dict（见 settings），键名与 dataclass 字段一致。
    """
    raw = getattr(settings, "UPLOAD_LIMITS", None) or {}
    known = {f for f in UploadLimits.__dataclass_fields__}
    filtered = {k: v for k, v in raw.items() if k in known and v is not None}
    return UploadLimits(**filtered)


# 材料类型的中文标签（页面与错误提示共用，避免各处各写一份）
MATERIAL_LABELS = {
    "text": "文字说明",
    "image": "图片证明",
    "file": "文件附件",
    "link": "作品链接",
    "video": "视频",
}

# 允许上传的真实内容类型（依据实际检测结果，不看客户端声明）。
# 图片由 imaging 模块用魔数+解码双重校验；这里约束的是非图片类文件。
ALLOWED_FILE_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".txt", ".csv", ".md", ".zip", ".7z",
}
