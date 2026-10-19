# -*- coding: utf-8 -*-
"""图片处理纯逻辑。

来源与复用说明
--------------
本模块的函数职责与参数取值**参照用户已提供的 campus-share 项目**
（`backend/app/routers/upload.py`）中的 `_detect_image_type()` 与
`_compress_image()`。源文件在本机可访问，已逐行核对，因此是**提取并适配**
而非凭空重写。具体对应关系：

    campus-share                     本模块
    --------------------------------- --------------------------------------
    IMAGE_MAGICS 字典                  MAGIC_PREFIXES（结构一致）
    _detect_image_type(header)        detect_image_format(data)
    _compress_image(contents)         process_image(data)
    常量 1280 / 82 / 400 / 78          同名默认参数，可被调用方覆盖

相对源文件的**扩展**（P3 的新要求，源项目没有）：
  1. 增加 WebP 输入支持（源项目只支持 jpeg/png）；
  2. 检测环节由「仅看文件头魔数」升级为「魔数 + Pillow 实际解码双重校验」，
     格式声明与真实内容不一致时拒绝；
  3. 把 Pillow 的解压炸弹告警升级为显式拒绝，而不是忽略；
  4. 返回结构化结果（含尺寸），便于落库与断言。

**未复制**源项目的 FastAPI、SQLAlchemy、Pydantic、JWT 相关代码。

设计约束（P3 明确要求）
----------------------
- 纯逻辑：输入 bytes → 输出不可变结果对象；不导入 Django、不导入云存储 SDK、
  不访问数据库。因此可独立单元测试，也能被视图 / 管理命令 / 后台任务共用。
- 不关闭 Pillow 的像素数量防护（不允许为了"支持大图"而放宽解压炸弹保护）。
- 所有非法输入统一抛 `ImageValidationError`（`ValueError` 子类），
  调用方据此返回用户级错误，**不得产生未捕获异常导致的 HTTP 500**。
"""
from __future__ import annotations

import io
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

# ---------------------------------------------------------------------------
# 默认参数（与源项目取值一致；调用方可覆盖，配置化由 upload_config 负责）
# ---------------------------------------------------------------------------
DEFAULT_MAX_EDGE = 1280        # 主图最大长边
DEFAULT_IMAGE_QUALITY = 82     # 主图 JPEG 质量
DEFAULT_THUMB_EDGE = 400       # 缩略图边长（正方形）
DEFAULT_THUMB_QUALITY = 78     # 缩略图 JPEG 质量

# 允许的输入格式（P3：JPEG / PNG / WebP）。其他格式一律拒绝。
SUPPORTED_FORMATS = ("jpeg", "png", "webp")

# 魔数（文件头特征）。与源项目的 IMAGE_MAGICS 对应，并补充 WebP。
# WebP 的容器头是 RIFF....WEBP，需要同时检查开头与第 8~11 字节。
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_RIFF_MAGIC = b"RIFF"
_WEBP_TAG = b"WEBP"

# Pillow 解码后 img.format 的取值 → 本模块的统一格式名
_PIL_FORMAT_MAP = {
    "JPEG": "jpeg",
    "MPO": "jpeg",      # 部分相机把 JPEG 存成 MPO，按 JPEG 处理
    "PNG": "png",
    "WEBP": "webp",
}


class ImageValidationError(ValueError):
    """图片校验失败。

    属于**可预期的用户级错误**：调用方应把它转成「请换一张图片」这类提示，
    而不是 500。message 面向最终用户，可直接展示。
    """


@dataclass(frozen=True)
class ProcessedImage:
    """图片处理结果（不可变）。"""

    main_bytes: bytes
    thumb_bytes: bytes
    source_format: str          # 实际（解码后确认）的输入格式
    declared_format: str        # 依据魔数推断的格式，用于一致性核对
    main_width: int
    main_height: int
    thumb_width: int
    thumb_height: int

    @property
    def stored_bytes(self) -> int:
        """真正需要持久化的字节数（主图 + 缩略图）。

        图片配额按这个数计算 —— 不是上传时的原始字节数。
        """
        return len(self.main_bytes) + len(self.thumb_bytes)


# ---------------------------------------------------------------------------
# 格式检测
# ---------------------------------------------------------------------------

def detect_image_format(data: bytes) -> str:
    """依据**文件真实内容**（魔数）判断图片格式。

    刻意不看扩展名、不看浏览器提交的 Content-Type、不看表单选择 ——
    这三者都由客户端控制，不能作为可信依据。

    返回 'jpeg' / 'png' / 'webp'；无法识别时抛 ImageValidationError。
    """
    if not data or len(data) < 12:
        raise ImageValidationError("文件内容为空或过短，不是有效图片。")

    if data.startswith(_JPEG_MAGIC):
        return "jpeg"
    if data.startswith(_PNG_MAGIC):
        return "png"
    if data[0:4] == _RIFF_MAGIC and data[8:12] == _WEBP_TAG:
        return "webp"

    raise ImageValidationError("仅支持 JPEG、PNG、WebP 格式的图片。")


def _open_and_verify(data: bytes):
    """打开并做真实解码校验，返回 Pillow 图像对象（已摆正方向）。

    与「只看魔数」相比，这一步能拦住：伪造文件头、截断文件、损坏内容。
    """
    # 把 Pillow 的解压炸弹告警升级为错误：
    # 默认它只是 warning，会被静默忽略，等于绕过了防护。
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            img = Image.open(io.BytesIO(data))
            img.verify()                      # 校验结构完整性（不完整解码）
        except Image.DecompressionBombWarning as exc:
            raise ImageValidationError("图片像素数过大，超出安全处理范围。") from exc
        except Image.DecompressionBombError as exc:
            raise ImageValidationError("图片像素数过大，超出安全处理范围。") from exc
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
            raise ImageValidationError("图片无法解析，请换一张图片试试。") from exc

    # verify() 之后图像对象不可再用，需要重新打开才能解码像素。
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            reopened = Image.open(io.BytesIO(data))
            reopened.load()                   # 真正解码像素，暴露截断/损坏
        except Image.DecompressionBombWarning as exc:
            raise ImageValidationError("图片像素数过大，超出安全处理范围。") from exc
        except Image.DecompressionBombError as exc:
            raise ImageValidationError("图片像素数过大，超出安全处理范围。") from exc
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
            raise ImageValidationError("图片无法解析，请换一张图片试试。") from exc

    return reopened


def _to_rgb(img: Image.Image) -> Image.Image:
    """统一转为 RGB。

    透明通道处理：先铺白底再合成，避免直接 convert("RGB") 把透明处变黑。
    这与源项目 `_compress_image` 里的做法一致（flattened.paste(img, mask=alpha)）。
    """
    if img.mode == "RGB":
        return img
    if img.mode in ("RGBA", "LA"):
        rgba = img.convert("RGBA")
        flattened = Image.new("RGB", rgba.size, (255, 255, 255))
        flattened.paste(rgba, mask=rgba.split()[-1])
        return flattened
    if img.mode == "P":
        # 调色板图可能带透明索引：转 RGBA 后按透明处理，否则直接转 RGB。
        rgba = img.convert("RGBA")
        if "transparency" in img.info:
            flattened = Image.new("RGB", rgba.size, (255, 255, 255))
            flattened.paste(rgba, mask=rgba.split()[-1])
            return flattened
        return rgba.convert("RGB")
    # 其余模式（L、CMYK、I;16 等）交给 Pillow 处理
    return img.convert("RGB")


def _center_square(img: Image.Image, edge: int) -> Image.Image:
    """按短边居中裁成正方形，再缩放到 edge×edge。

    用裁切而不是拉伸 —— 拉伸会导致人像/海报明显变形。
    与源项目的做法一致。
    """
    side = min(img.size)
    left = (img.width - side) // 2
    top = (img.height - side) // 2
    cropped = img.crop((left, top, left + side, top + side))
    return cropped.resize((edge, edge), Image.LANCZOS)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def process_image(
    data: bytes,
    *,
    max_edge: int = DEFAULT_MAX_EDGE,
    image_quality: int = DEFAULT_IMAGE_QUALITY,
    thumb_edge: int = DEFAULT_THUMB_EDGE,
    thumb_quality: int = DEFAULT_THUMB_QUALITY,
) -> ProcessedImage:
    """把上传的原图处理成「主图 + 方形缩略图」（均为 JPEG）。

    步骤（顺序不可调换）：
        1. 魔数检测 → 得到声明格式
        2. 真实解码 → 得到实际格式，两者不一致则拒绝
        3. EXIF 方向修正（手机竖拍照片不躺倒）
        4. 颜色模式统一为 RGB（透明垫白底）
        5. 主图：长边压到 max_edge 以内，长宽比不变，JPEG 质量 image_quality
        6. 缩略图：短边居中裁方，缩到 thumb_edge×thumb_edge，JPEG 质量 thumb_quality

    任何一步失败都抛 ImageValidationError，不抛原始 Pillow 异常。
    """
    declared = detect_image_format(data)

    img = _open_and_verify(data)

    actual_raw = (img.format or "").upper()
    actual = _PIL_FORMAT_MAP.get(actual_raw)
    if actual is None or actual not in SUPPORTED_FORMATS:
        raise ImageValidationError("仅支持 JPEG、PNG、WebP 格式的图片。")
    if actual != declared:
        # 例：PNG 文件头 + JPEG 内容，或故意拼接的混合文件
        raise ImageValidationError("文件内容与声明的图片格式不一致，已拒绝。")

    try:
        # EXIF 方向修正：源项目同样在压缩前处理，否则竖图会躺倒
        oriented = ImageOps.exif_transpose(img) or img
        rgb = _to_rgb(oriented)

        # --- 主图 ---
        main_img = rgb.copy()
        main_img.thumbnail((max_edge, max_edge), Image.LANCZOS)
        main_buffer = io.BytesIO()
        main_img.save(
            main_buffer, "JPEG",
            quality=image_quality, optimize=True, progressive=True,
        )

        # --- 缩略图（用未缩小的 rgb，保证裁切来源是原始比例）---
        thumb_img = _center_square(rgb, thumb_edge)
        thumb_buffer = io.BytesIO()
        thumb_img.save(
            thumb_buffer, "JPEG",
            quality=thumb_quality, optimize=True, progressive=True,
        )
    except ImageValidationError:
        raise
    except (OSError, ValueError, MemoryError) as exc:
        # 编码阶段的异常同样归为可预期的校验失败
        raise ImageValidationError("图片处理失败，请换一张图片试试。") from exc

    return ProcessedImage(
        main_bytes=main_buffer.getvalue(),
        thumb_bytes=thumb_buffer.getvalue(),
        source_format=actual,
        declared_format=declared,
        main_width=main_img.width,
        main_height=main_img.height,
        thumb_width=thumb_img.width,
        thumb_height=thumb_img.height,
    )
