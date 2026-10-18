"""
自定义用户模型与角色。

设计要点（对应验收 B）：
1. **本模型必须在第一批迁移中建立** —— accounts/migrations/0001_initial.py
   就是在它定义好之后生成的，因此不存在"先建了默认 User 再换掉"的历史负担。
2. **不用单一枚举锁死身份** —— 一个人可能既是队员又是管理员。
   因此角色用「多对多 + 角色表」表达，而不是 user.role = "admin" 这种单值字段。
3. **账号由管理员创建**，无公开自助注册（见 urls.py 只暴露登录/退出/改密）。
4. **密码走 Django 正规哈希**（PBKDF2），任何地方都不保存明文。
5. **首登强制改密** —— must_change_password 标记 + 中间件拦截（见 middleware.py）。
"""
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class Role(models.Model):
    """角色字典表。

    用独立表而不是硬编码枚举，好处是：
    - 新增角色（如"裁判""物资管理员"）不需要改代码、不需要数据迁移；
    - 为后续 P2 的权限分配留出扩展边界；
    - 兼容多重身份（一个用户可挂多个角色）。
    """

    code = models.SlugField(
        "角色标识", max_length=50, unique=True,
        help_text="英文标识，程序内引用用，例如 coach / admin / player",
    )
    name = models.CharField("角色名称", max_length=50)
    description = models.CharField("说明", max_length=200, blank=True)
    sort_order = models.IntegerField("排序", default=100)
    is_active = models.BooleanField("启用", default=True)

    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        verbose_name = "角色"
        verbose_name_plural = "角色"
        ordering = ["sort_order", "code"]

    def __str__(self):
        return self.name


class UserManager(BaseUserManager):
    """用户管理器：把"学号"作为唯一登录标识。"""

    use_in_migrations = True

    def _create_user(self, student_id, password, **extra):
        if not student_id:
            raise ValueError("必须提供学号")
        student_id = str(student_id).strip()
        user = self.model(student_id=student_id, **extra)
        # set_password 走 Django 的密码哈希（PBKDF2），绝不保存明文
        user.set_password(password)
        # 刻意不调用 full_clean()：
        #   create_user 属于程序化创建路径（脚本/测试/后台），
        #   模型级必填校验（如 real_name）应由表单层负责。
        #   在此强制 full_clean 会让最基础的建号操作因缺姓名而失败，
        #   也会把唯一性检查降级为 Python 层判断，掩盖数据库约束的作用。
        user.save(using=self._db)
        return user

    def create_user(self, student_id, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(student_id, password, **extra)

    def create_superuser(self, student_id, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if extra.get("is_staff") is not True:
            raise ValueError("超级用户必须 is_staff=True")
        if extra.get("is_superuser") is not True:
            raise ValueError("超级用户必须 is_superuser=True")
        return self._create_user(student_id, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """自定义用户模型（AUTH_USER_MODEL = accounts.User）。

    登录标识为学号（student_id），不依赖邮箱。
    """

    student_id = models.CharField(
        "学号", max_length=32, unique=True,
        help_text="校内学号或工号，登录时使用",
    )
    real_name = models.CharField("姓名", max_length=50)
    phone = models.CharField("手机号", max_length=20, blank=True)
    email = models.EmailField("邮箱", blank=True)

    roles = models.ManyToManyField(
        Role, verbose_name="角色", related_name="users", blank=True,
        help_text="一个用户可以同时拥有多个角色",
    )

    is_active = models.BooleanField("账号启用", default=True)
    is_staff = models.BooleanField(
        "可登录后台", default=False,
        help_text="是否允许进入 Django 管理后台",
    )

    must_change_password = models.BooleanField(
        "首次登录须改密", default=True,
        help_text="管理员创建账号后置为 True；用户改密成功后自动置为 False",
    )
    password_changed_at = models.DateTimeField("上次改密时间", null=True, blank=True)

    date_joined = models.DateTimeField("创建时间", default=timezone.now)
    last_login_ip = models.GenericIPAddressField("上次登录 IP", null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "student_id"
    REQUIRED_FIELDS = ["real_name"]

    class Meta:
        verbose_name = "用户"
        verbose_name_plural = "用户"
        ordering = ["student_id"]
        indexes = [
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return "%s(%s)" % (self.real_name or "-", self.student_id)

    # --- 便捷方法：角色判断不写死在枚举里，而是查角色表 ---

    def role_codes(self):
        """返回该用户所有启用角色的标识集合。"""
        return set(self.roles.filter(is_active=True).values_list("code", flat=True))

    def has_role(self, *codes):
        """是否拥有其中任一角色（支持多重身份，不用单一枚举）。"""
        if not codes:
            return False
        if self.is_superuser:
            return True
        return bool(self.role_codes() & set(codes))

    def get_short_name(self):
        return self.real_name or self.student_id

    def get_full_name(self):
        return "%s（%s）" % (self.real_name, self.student_id)

    def mark_password_changed(self):
        """改密成功后调用：清除首登标记并记录时间。"""
        self.must_change_password = False
        self.password_changed_at = timezone.now()
        self.save(update_fields=["must_change_password", "password_changed_at"])
