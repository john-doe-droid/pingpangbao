# -*- coding: utf-8 -*-
"""submissions 表单。

安全约定：
- 提交人、状态、逾期标记、存储键**一律不由表单接收** ——
  它们全部由后端根据登录身份与提交时刻计算（P3 明确要求）。
- 表单只负责接收用户真正能决定的东西：文字、文件、链接。
"""
from django import forms

from core.upload_config import MATERIAL_LABELS, get_upload_limits, human_size


class MultipleFileInput(forms.ClearableFileInput):
    """支持多选的 ClearableFileInput。

    Django 标准库的 `ClearableFileInput` 会显式拒绝 `multiple`（见
    django/forms/widgets.py 的 ValueError），但多文件上传本身是支持的 ——
    只要 widget 允许 multiple，并用 `getlist(name)` 取值即可。
    官方文档 "Uploading multiple files" 给出的就是这个做法。

    这里不继承 ClearableFileInput 的 multi 校验，因此需要自己实现
    `allow_multiple_selected = True`。
    """

    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """接收多文件上传的 FileField。

    `clean()` 逐个校验每个文件（大小、类型），返回列表。
    与 `Widget.attrs["multiple"]` 配合使用。
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            result = [single_clean(item, initial) for item in data if item]
            # 空列表时返回 None，让 required 语义与普通字段一致
            return result or None
        return single_clean(data, initial)


class SubmissionForm(forms.Form):
    """新建提交 / 重新提交共用的表单。

    材料字段（images / files / video / link）是否**必填**由任务规则快照决定，
    因此这里全部设为可选，必填校验交给服务层（它读的是快照，不是模板当前值）。
    """

    text_content = forms.CharField(
        label="文字说明",
        required=False,
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 5,
            "placeholder": "描述你完成的工作内容、思路或注意事项",
        }),
    )

    images = MultipleFileField(
        label="图片证明",
        required=False,
        widget=MultipleFileInput(attrs={
            "class": "form-control",
            "accept": "image/jpeg,image/png,image/webp",
            "multiple": True,
        }),
    )

    files = MultipleFileField(
        label="文件附件",
        required=False,
        widget=MultipleFileInput(attrs={
            "class": "form-control",
            "multiple": True,
        }),
    )

    link = forms.URLField(
        label="作品链接",
        required=False,
        assume_scheme="https",
        widget=forms.URLInput(attrs={
            "class": "form-control",
            "placeholder": "https://...",
        }),
    )

    def __init__(self, *args, task=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.task = task
        limits = get_upload_limits()
        snapshot = (task.rule_snapshot if task else {}) or {}

        # 依据快照决定哪些字段出现在表单上：
        # 不允许的材料类型直接不渲染，避免用户白填
        if task is not None:
            if not snapshot.get("allow_text"):
                self.fields.pop("text_content", None)
            if not snapshot.get("allow_image"):
                self.fields.pop("images", None)
            if not snapshot.get("allow_file"):
                self.fields.pop("files", None)
            if not snapshot.get("allow_link"):
                self.fields.pop("link", None)

        # 必填标记（用于模板显示红星）——真正的强制校验在服务层
        for key, field_name in (("text", "text_content"), ("image", "images"),
                                ("file", "files"), ("link", "link"),
                                ):
            if snapshot.get("require_%s" % key) and field_name in self.fields:
                self.fields[field_name].required = True
                self.fields[field_name].widget.attrs["required"] = "required"

        # 把上限写进 help_text —— 页面必须明确展示限制（P3 要求）
        if "images" in self.fields:
            self.fields["images"].help_text = (
                "支持 JPEG/PNG/WebP，单张不超过 %s；"
                "每学期最多 %d 张、%s。"
                % (human_size(limits.image_max_bytes),
                   limits.images_per_user,
                   human_size(limits.image_bytes_per_user))
            )
        if "files" in self.fields:
            self.fields["files"].help_text = "单个文件不超过 %s。" % human_size(
                limits.file_max_bytes)

    def clean_text_content(self):
        value = self.cleaned_data.get("text_content") or ""
        max_chars = get_upload_limits().text_max_chars
        if len(value) > max_chars:
            raise forms.ValidationError("文字说明不能超过 %d 个字符。" % max_chars)
        return value

    def required_materials(self):
        """返回本任务要求必填的材料标签（供页面提示）。"""
        snapshot = (self.task.rule_snapshot if self.task else {}) or {}
        return [
            MATERIAL_LABELS[key] for key in ("text", "image", "file", "link")
            if snapshot.get("require_%s" % key)
        ]


class ReviewForm(forms.Form):
    """审核表单：通过或退回。

    退回理由必填由服务层与数据库约束双重保证，
    这里只负责收集，并在需要时做非空提示。
    """

    ACTION_CHOICES = (("approve", "通过"), ("reject", "退回修改"))

    action = forms.ChoiceField(
        label="审核结果",
        choices=ACTION_CHOICES,
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )
    comment = forms.CharField(
        label="审核意见",
        required=False,
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 3,
            "placeholder": "选择「退回修改」时必须填写理由",
        }),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("action") == "reject" and not (cleaned.get("comment") or "").strip():
            self.add_error("comment", "退回修改必须填写理由，请说明需要改进的地方。")
        max_chars = get_upload_limits().comment_max_chars
        if len(cleaned.get("comment") or "") > max_chars:
            self.add_error("comment", "审核意见不能超过 %d 个字符。" % max_chars)
        return cleaned


class ReopenForm(forms.Form):
    """发起重新审核：必须填写原因。"""

    reopen_reason = forms.CharField(
        label="重新审核原因",
        widget=forms.Textarea(attrs={
            "class": "form-control",
            "rows": 3,
            "placeholder": "说明为什么要对已通过的记录重新审核",
        }),
    )
    comment = forms.CharField(
        label="审核意见",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )

    def clean_reopen_reason(self):
        value = (self.cleaned_data.get("reopen_reason") or "").strip()
        if not value:
            raise forms.ValidationError("发起重新审核必须填写原因。")
        max_chars = get_upload_limits().comment_max_chars
        if len(value) > max_chars:
            raise forms.ValidationError(
                "重新审核原因不能超过 %d 个字符。" % max_chars,
            )
        return value

    def clean(self):
        cleaned = super().clean()
        max_chars = get_upload_limits().comment_max_chars
        if len(cleaned.get("comment") or "") > max_chars:
            self.add_error("comment", "审核意见不能超过 %d 个字符。" % max_chars)
        return cleaned
