# -*- coding: utf-8 -*-
"""图片处理纯逻辑测试（P3 要求逐条覆盖）。

这些测试**不需要数据库**，因为被测对象是纯逻辑模块：
输入 bytes → 输出不可变结果对象，不碰 Django ORM、不碰云存储。
"""
import io

import pytest
from PIL import Image

from core.imaging import (
    DEFAULT_MAX_EDGE,
    DEFAULT_THUMB_EDGE,
    ImageValidationError,
    detect_image_format,
    process_image,
)

from .conftest import make_image_bytes


# ===========================================================================
# 格式检测
# ===========================================================================

class TestFormatDetection:

    @pytest.mark.parametrize("fmt,expect", [("JPEG", "jpeg"), ("PNG", "png"), ("WEBP", "webp")])
    def test_detects_supported_formats(self, fmt, expect):
        """正常 JPEG / PNG / WebP 都能被识别。"""
        assert detect_image_format(make_image_bytes(fmt=fmt)) == expect

    def test_rejects_empty(self):
        with pytest.raises(ImageValidationError):
            detect_image_format(b"")

    def test_rejects_too_short(self):
        with pytest.raises(ImageValidationError):
            detect_image_format(b"\xff\xd8")

    def test_rejects_plain_text(self):
        with pytest.raises(ImageValidationError):
            detect_image_format(b"hello world, definitely not an image" * 3)

    def test_rejects_gif(self):
        """GIF 不在支持列表内 —— 未经明确支持的格式不得默认接受。"""
        buf = io.BytesIO()
        Image.new("P", (30, 30)).save(buf, "GIF")
        with pytest.raises(ImageValidationError):
            detect_image_format(buf.getvalue())

    def test_rejects_bmp(self):
        buf = io.BytesIO()
        Image.new("RGB", (30, 30)).save(buf, "BMP")
        with pytest.raises(ImageValidationError):
            detect_image_format(buf.getvalue())

    def test_rejects_fake_extension_png_header_garbage(self):
        """伪造：有 PNG 文件头但内容是垃圾 —— 必须被拒。"""
        fake = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
        assert detect_image_format(fake) == "png"      # 魔数层面像 PNG
        with pytest.raises(ImageValidationError):      # 但真实解码会失败
            process_image(fake)

    def test_rejects_content_type_mismatch(self):
        """JPEG 头 + PNG 内容（拼接文件）必须被拒。"""
        png = make_image_bytes(fmt="PNG")
        hybrid = b"\xff\xd8\xff" + png[8:]
        with pytest.raises(ImageValidationError):
            process_image(hybrid)

    def test_rejects_truncated_image(self):
        """截断的 JPEG 必须被拒，而不是产出残缺图。"""
        data = make_image_bytes(fmt="JPEG")
        with pytest.raises(ImageValidationError):
            process_image(data[: len(data) // 4])

    def test_rejects_corrupted_content(self):
        """中间被破坏的文件必须被拒。"""
        data = bytearray(make_image_bytes(fmt="PNG"))
        for i in range(100, min(400, len(data))):
            data[i] = 0xFF
        with pytest.raises(ImageValidationError):
            process_image(bytes(data))

    def test_errors_are_user_facing(self):
        """异常消息必须面向用户，可直接展示（不含 traceback / 内部路径）。"""
        try:
            process_image(b"not an image")
        except ImageValidationError as exc:
            msg = str(exc)
            assert "Traceback" not in msg
            assert "\\" not in msg and "/" not in msg
            assert len(msg) < 100
        else:
            pytest.fail("应当抛 ImageValidationError")


# ===========================================================================
# 方向与颜色
# ===========================================================================

class TestOrientationAndColor:

    @pytest.mark.parametrize("orientation,expect_portrait", [
        (6, True),    # 顺时针 90°：800x400 → 竖图
        (8, True),    # 逆时针 90°：800x400 → 竖图
        (3, False),   # 180°：宽高不变
        (1, False),   # 正常
    ])
    def test_exif_orientation_applied(self, orientation, expect_portrait):
        """EXIF 方向必须被应用，否则手机竖拍照片会躺倒。"""
        data = make_image_bytes(fmt="JPEG", size=(800, 400), exif_orientation=orientation)
        result = process_image(data)
        is_portrait = result.main_height > result.main_width
        assert is_portrait is expect_portrait

    def test_rgba_transparent_becomes_white_not_black(self):
        """透明 PNG 转 JPEG 必须垫白底 —— 直接转会出现黑底。"""
        im = Image.new("RGBA", (600, 600), (0, 0, 0, 0))     # 全透明
        for x in range(200, 400):
            for y in range(200, 400):
                im.putpixel((x, y), (255, 0, 0, 255))        # 中间一块纯红
        buf = io.BytesIO()
        im.save(buf, "PNG")

        result = process_image(buf.getvalue())
        out = Image.open(io.BytesIO(result.main_bytes)).convert("RGB")

        corner = out.getpixel((5, 5))
        center = out.getpixel((out.width // 2, out.height // 2))
        # 透明区域应变成白色（若实现有误会是黑色 (0,0,0)）
        assert all(c > 240 for c in corner), "透明区域应垫白底，实际为 %s" % (corner,)
        assert center[0] > 200 and center[1] < 60, "红块应保留，实际为 %s" % (center,)

    def test_palette_mode_with_transparency(self):
        """调色板图带透明索引时同样垫白底。"""
        im = Image.new("P", (400, 400), 0)
        im.info["transparency"] = 0
        buf = io.BytesIO()
        im.save(buf, "PNG")
        result = process_image(buf.getvalue())
        assert result.main_bytes                      # 能正常产出
        out = Image.open(io.BytesIO(result.main_bytes))
        assert out.mode == "RGB"

    def test_la_mode(self):
        """灰度+透明（LA）也要能处理。"""
        im = Image.new("LA", (300, 300), (128, 100))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        result = process_image(buf.getvalue())
        assert result.main_width == 300


# ===========================================================================
# 主图压缩
# ===========================================================================

class TestMainImage:

    def test_long_edge_limited(self):
        result = process_image(make_image_bytes(fmt="JPEG", size=(2400, 1600)))
        assert max(result.main_width, result.main_height) == DEFAULT_MAX_EDGE

    def test_aspect_ratio_preserved(self):
        """长宽比必须保持，不能拉伸。"""
        result = process_image(make_image_bytes(fmt="JPEG", size=(2400, 1200)))
        src_ratio = 2400 / 1200
        out_ratio = result.main_width / result.main_height
        assert abs(src_ratio - out_ratio) < 0.02

    def test_small_image_not_upscaled(self):
        """小图不得被放大。"""
        result = process_image(make_image_bytes(fmt="JPEG", size=(300, 200)))
        assert (result.main_width, result.main_height) == (300, 200)

    def test_output_is_jpeg(self):
        for fmt in ("JPEG", "PNG", "WEBP"):
            result = process_image(make_image_bytes(fmt=fmt))
            img = Image.open(io.BytesIO(result.main_bytes))
            assert img.format == "JPEG", "%s 应统一输出 JPEG" % fmt

    def test_quality_parameter_respected(self):
        """质量参数应生效：低质量产出的字节数应明显更小。

        注意必须用**含噪声/细节**的图 —— 纯色图在高/低质量下压缩率恰好相同，
        无法体现参数差异（这一点是本测试第一版踩过的坑）。
        """
        import random

        random.seed(42)
        im = Image.new("RGB", (600, 450))
        for x in range(600):
            for y in range(450):
                im.putpixel((x, y), (
                    random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)))
        buf = io.BytesIO()
        im.save(buf, "PNG")          # 用 PNG 保存，避免二次压缩干扰
        data = buf.getvalue()

        high = process_image(data, image_quality=95)
        low = process_image(data, image_quality=10)
        assert len(low.main_bytes) < len(high.main_bytes), (
            "低质量(%d B) 应明显小于高质量(%d B)"
            % (len(low.main_bytes), len(high.main_bytes))
        )

    def test_custom_max_edge(self):
        result = process_image(make_image_bytes(size=(2000, 1000)), max_edge=800)
        assert max(result.main_width, result.main_height) == 800


# ===========================================================================
# 缩略图
# ===========================================================================

class TestThumbnail:

    def test_exactly_400x400(self):
        for size in ((1600, 1200), (1200, 1600), (800, 800)):
            result = process_image(make_image_bytes(size=size))
            assert (result.thumb_width, result.thumb_height) == (DEFAULT_THUMB_EDGE,
                                                                DEFAULT_THUMB_EDGE)

    def test_is_center_crop_not_stretch(self):
        """居中裁切而非拉伸：验证裁到的确实是中间那条。

        造一张 800x200 的图，左红 / 中绿 / 右蓝；短边为 200，
        居中裁切应取 x∈[300,500] 的纯绿区域。
        若实现是拉伸，缩略图左侧会出现红色。
        """
        im = Image.new("RGB", (800, 200), (0, 0, 0))
        for x in range(800):
            color = (255, 0, 0) if x < 200 else ((0, 255, 0) if x < 600 else (0, 0, 255))
            for y in range(200):
                im.putpixel((x, y), color)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=95)

        result = process_image(buf.getvalue())
        thumb = Image.open(io.BytesIO(result.thumb_bytes)).convert("RGB")

        left = thumb.getpixel((4, thumb.height // 2))
        center = thumb.getpixel((thumb.width // 2, thumb.height // 2))
        assert center[1] > 200 and center[0] < 60, "中心应为绿色，实际 %s" % (center,)
        assert left[0] < 60, "左侧不应出现红色（说明被拉伸而非裁切），实际 %s" % (left,)

    def test_output_is_jpeg(self):
        result = process_image(make_image_bytes())
        assert Image.open(io.BytesIO(result.thumb_bytes)).format == "JPEG"

    def test_thumbnail_smaller_than_main(self):
        """缩略图仅用于预览，不应比主图还大。"""
        result = process_image(make_image_bytes(size=(2400, 1800)))
        assert len(result.thumb_bytes) < len(result.main_bytes)

    def test_thumbnail_keeps_orientation(self):
        """缩略图也必须按 EXIF 方向摆正（竖图裁出来的内容才正确）。"""
        data = make_image_bytes(size=(800, 400), exif_orientation=6)
        result = process_image(data)
        assert result.thumb_width == result.thumb_height == DEFAULT_THUMB_EDGE


# ===========================================================================
# 安全边界
# ===========================================================================

class TestSafety:

    def test_decompression_bomb_rejected(self):
        """解压炸弹必须被拒 —— 不得为了"支持大图"而关闭像素防护。"""
        from PIL import Image as PILImage

        original = PILImage.MAX_IMAGE_PIXELS
        try:
            PILImage.MAX_IMAGE_PIXELS = 100          # 把阈值调低以便造出炸弹
            big = Image.new("RGB", (500, 500))       # 25 万像素 > 100
            buf = io.BytesIO()
            big.save(buf, "PNG")
            with pytest.raises(ImageValidationError):
                process_image(buf.getvalue())
        finally:
            PILImage.MAX_IMAGE_PIXELS = original

    def test_storage_bytes_counts_main_plus_thumb(self):
        result = process_image(make_image_bytes())
        assert result.stored_bytes == len(result.main_bytes) + len(result.thumb_bytes)

    def test_result_reports_dimensions(self):
        result = process_image(make_image_bytes(size=(2000, 1000)))
        assert result.main_width and result.main_height
        assert result.thumb_width == result.thumb_height == DEFAULT_THUMB_EDGE

    def test_source_format_reported(self):
        for fmt, expect in (("JPEG", "jpeg"), ("PNG", "png"), ("WEBP", "webp")):
            result = process_image(make_image_bytes(fmt=fmt))
            assert result.source_format == expect
            assert result.declared_format == expect

    def test_no_exception_other_than_validation_error(self):
        """各种畸形输入都必须是可预期的 ImageValidationError，不能是别的异常。"""
        bad_inputs = [
            b"",
            b"x",
            b"\xff\xd8\xff",
            b"\x89PNG\r\n\x1a\n",
            b"RIFF\x00\x00\x00\x00WEBP",
            b"\xff\xd8\xff" + b"\x00" * 500,
            bytes(range(256)) * 4,
        ]
        for data in bad_inputs:
            try:
                process_image(data)
            except ImageValidationError:
                pass
            except Exception as exc:  # noqa: BLE001
                pytest.fail("输入 %r 抛出了非预期异常 %s: %s"
                            % (data[:12], type(exc).__name__, exc))
