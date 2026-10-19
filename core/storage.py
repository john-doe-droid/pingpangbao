# -*- coding: utf-8 -*-
"""私有文件存储适配层。

目的（P3 明确要求）
------------------
让业务代码**不直接依赖某个具体 COS SDK 的调用细节**。业务侧只调用本模块的
`get_storage()`，拿到的对象提供统一的 save / delete / exists / open /
presigned_url 接口。

两个实现：
    LocalPrivateStorage —— 本地磁盘，用于开发与测试（文件放在 MEDIA_ROOT 之外的
                          私有目录，**不能**通过 /media/ 或 /static/ 直接访问）
    CosPrivateStorage   —— 腾讯云 COS 私有对象存储，用于生产

关键安全行为
------------
1. 生产模式缺少必要 COS 配置时**明确报错，不静默回退本地**。
   （静默回退会让"私有存储"变成"写在服务器磁盘上还能被直链"，属严重问题）
2. 对象键一律由服务端生成（uuid.jpg / uuid_thumb.jpg），
   **不接受客户端提供的任何路径成分**。
3. 下载一律走 `presigned_url()` 生成短时链接；页面里不出现 COS 永久公开地址。
"""
from __future__ import annotations

import os
import shutil
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from django.conf import settings


class StorageConfigError(RuntimeError):
    """存储配置错误。

    属于**部署级错误**，必须让启动或调用立刻失败并给出可操作的说明，
    不能降级成"先用本地磁盘凑合"。
    """


# 本地下载链接的签名盐值：用于校验 token 确实由本服务签发
DOWNLOAD_SALT = "pingpong.private-download.v1"


def verify_download_token(token: str) -> str | None:
    """校验本地下载 token。

    返回对象键；token 无效、被篡改或**已过期**时返回 None。
    过期判定用签名时间戳（signing 的 max_age），无需额外查库。
    """
    from django.core import signing

    from core.upload_config import get_upload_limits

    max_age = get_upload_limits().presign_expires_seconds
    try:
        payload = signing.loads(token, salt=DOWNLOAD_SALT, max_age=max_age)
    except signing.SignatureExpired:
        return None          # 已过期 —— 明确失效，不返回内容
    except signing.BadSignature:
        return None          # 被篡改
    key = payload.get("key") if isinstance(payload, dict) else None
    return key or None


def generate_object_key(suffix: str = ".jpg", *, thumb: bool = False) -> str:
    """生成服务端对象键。

    格式：`<uuid>.jpg` 或 `<uuid>_thumb.jpg`
    刻意不使用任何来自用户输入的部分（原始文件名、扩展名、路径），
    从根源上避免目录穿越与对象键注入。
    """
    stem = uuid.uuid4().hex
    if thumb:
        stem += "_thumb"
    return "%s%s" % (stem, suffix)


class BasePrivateStorage(ABC):
    """私有存储接口。"""

    @abstractmethod
    def save(self, key: str, content: bytes) -> None:
        """写入对象。key 必须由 generate_object_key 生成。"""

    @abstractmethod
    def save_from_path(self, key: str, path: str) -> None:
        """从本地文件写入对象（大文件用，避免整体读进内存）。"""

    @abstractmethod
    def delete(self, key: str) -> bool:
        """删除对象；返回是否确实删除了。"""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """对象是否存在。"""

    @abstractmethod
    def read(self, key: str) -> bytes:
        """读取对象全部内容（仅用于小文件与测试）。"""

    @abstractmethod
    def presigned_url(self, key: str, *, expires: int | None = None,
                      download_name: str | None = None) -> str:
        """生成短时下载链接。"""

    @abstractmethod
    def open_local_path(self, key: str):
        """返回一个可读的本地路径（用于内部处理），不存在时返回 None。"""


# ---------------------------------------------------------------------------
# 本地实现（开发 / 测试 / 无 COS 环境）
# ---------------------------------------------------------------------------

class LocalPrivateStorage(BasePrivateStorage):
    """本地私有目录。

    存放位置由 settings.PRIVATE_MEDIA_ROOT 指定，**不在 STATIC/MEDIA 之下**，
    因此不会被 Nginx 或 WhiteNoise 直接服务 —— 必须经过视图鉴权后由 Django 下发。
    """

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # 双保险：即便有人绕过 generate_object_key 传入可疑 key，也拒绝路径分隔符
        if "/" in key or "\\" in key or ".." in key:
            raise StorageConfigError("非法对象键：%r" % key)
        return self.root / key

    def save(self, key: str, content: bytes) -> None:
        path = self._path(key)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(content)
        os.replace(tmp, path)      # 原子落盘，避免半截文件

    def save_from_path(self, key: str, path: str) -> None:
        dest = self._path(key)
        shutil.copyfile(path, dest)

    def delete(self, key: str) -> bool:
        path = self._path(key)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def presigned_url(self, key: str, *, expires: int | None = None,
                      download_name: str | None = None) -> str:
        """本地实现返回**带签名与有效期**的站内下载地址。

        语义与 COS 预签名链接保持一致（一次授权、短时有效），
        因此同样满足「下载授权到期后不再有效」的要求 ——
        而不是给一个永不过期的裸链接。

        链接指向的视图仍会校验登录身份与访问权限（双重保护）。
        """
        from urllib.parse import quote

        from django.core import signing
        from django.urls import reverse
        from django.utils import timezone

        from core.upload_config import get_upload_limits

        expire = expires or get_upload_limits().presign_expires_seconds
        expires_at = int(timezone.now().timestamp()) + int(expire)
        token = signing.dumps(
            {"key": key, "exp": expires_at}, salt=DOWNLOAD_SALT, compress=True,
        )
        url = reverse("core:private_file", kwargs={"key": key})
        url += "?token=" + quote(token)
        if download_name:
            url += "&name=" + quote(download_name)
        return url

    def open_local_path(self, key: str):
        path = self._path(key)
        return path if path.exists() else None

    def iter_keys(self):
        """列出全部对象键（仅本地实现提供，供一致性核对脚本使用）。"""
        return [p.name for p in self.root.iterdir() if p.is_file()]


# ---------------------------------------------------------------------------
# COS 实现（生产）
# ---------------------------------------------------------------------------

class CosPrivateStorage(BasePrivateStorage):
    """腾讯云 COS 私有存储。

    依赖 cos-python-sdk-v5（版本在 requirements.txt 中锁定，并已实测
    在 Python 3.13 下可导入、具备所需 API）。

    安全要点：
    - Bucket 必须为**私有读写**；本类从不生成永久公开 URL。
    - 密钥只从配置读取，不写死在代码里。
    - 下载走 get_presigned_url，默认 5 分钟有效。
    """

    def __init__(self, *, region: str, bucket: str, secret_id: str,
                 secret_key: str, scheme: str = "https",
                 temp_dir: str | Path | None = None):
        if not all([region, bucket, secret_id, secret_key]):
            raise StorageConfigError(
                "COS 配置不完整：需要 region / bucket / secret_id / secret_key 四项。"
            )
        # 延迟导入：没装 SDK 且不使用 COS 的环境（开发机）不应因此无法启动
        try:
            from qcloud_cos import CosConfig, CosS3Client
        except ImportError as exc:
            raise StorageConfigError(
                "未安装 cos-python-sdk-v5，无法启用 COS 存储。"
                "请执行：pip install -r requirements.txt"
            ) from exc

        self.bucket = bucket
        self.region = region
        self._temp_dir = Path(temp_dir) if temp_dir else None
        config = CosConfig(
            Region=region, SecretId=secret_id, SecretKey=secret_key, Scheme=scheme,
        )
        self._client = CosS3Client(config)

    def save(self, key: str, content: bytes) -> None:
        self._client.put_object(Bucket=self.bucket, Key=key, Body=content)

    def save_from_path(self, key: str, path: str) -> None:
        # upload_file 内部会分块上传，不会把整个大文件读进内存
        self._client.upload_file(
            Bucket=self.bucket, Key=key, LocalFilePath=str(path),
            PartSize=10, MAXThread=4, EnableMD5=False,
        )

    def delete(self, key: str) -> bool:
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:  # noqa: BLE001 —— SDK 异常类型较多，统一按删除失败处理
            # 删除失败由调用方记录为"待清理"，不假装成功
            return False

    def exists(self, key: str) -> bool:
        try:
            return self._client.object_exists(Bucket=self.bucket, Key=key)
        except Exception:  # noqa: BLE001
            return False

    def read(self, key: str) -> bytes:
        resp = self._client.get_object(Bucket=self.bucket, Key=key)
        return resp["Body"].get_raw_stream().read()

    def presigned_url(self, key: str, *, expires: int | None = None,
                      download_name: str | None = None) -> str:
        from core.upload_config import get_upload_limits

        expire = expires or get_upload_limits().presign_expires_seconds
        params = {"Bucket": self.bucket, "Key": key, "Expired": expire}
        if download_name:
            # 让浏览器按原始文件名下载，而不是 uuid.jpg
            params["ResponseContentDisposition"] = (
                'attachment; filename="%s"' % download_name.replace('"', "")
            )
        return self._client.get_presigned_url(Method="GET", **params)

    def open_local_path(self, key: str):
        """COS 对象没有本地路径。

        需要本地文件的场景（如测试）应使用 LocalPrivateStorage；
        这里返回 None，调用方据此走流式读取而不是假设本地存在。
        """
        return None


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

_storage_singleton = None


def build_storage():
    """按配置构造存储实例（不缓存）。"""
    backend = (getattr(settings, "PRIVATE_STORAGE_BACKEND", "") or "").strip().lower()

    if backend in ("cos", "qcloud", "tencent"):
        cfg = getattr(settings, "COS_CONFIG", None) or {}
        return CosPrivateStorage(
            region=cfg.get("region", ""),
            bucket=cfg.get("bucket", ""),
            secret_id=cfg.get("secret_id", ""),
            secret_key=cfg.get("secret_key", ""),
            scheme=cfg.get("scheme", "https"),
        )

    if backend in ("local", "filesystem", ""):
        # 生产环境**不允许**落到本地实现 —— 那等于私有存储没有落地
        if not getattr(settings, "DEBUG", False) and backend == "":
            raise StorageConfigError(
                "生产环境必须显式配置 PRIVATE_STORAGE_BACKEND=cos。"
                "当前未配置任何存储后端，已拒绝回退到本地磁盘。"
            )
        root = getattr(settings, "PRIVATE_MEDIA_ROOT", None)
        if not root:
            raise StorageConfigError("缺少 PRIVATE_MEDIA_ROOT 配置，无法使用本地私有存储。")
        return LocalPrivateStorage(Path(root))

    raise StorageConfigError("未知的 PRIVATE_STORAGE_BACKEND：%r" % backend)


def get_storage():
    """获取存储实例（进程内缓存，避免每次请求重建 COS 客户端）。"""
    global _storage_singleton
    if _storage_singleton is None:
        _storage_singleton = build_storage()
    return _storage_singleton


def reset_storage_cache():
    """清空缓存（测试用：切换后端后需要重新构造）。"""
    global _storage_singleton
    _storage_singleton = None
