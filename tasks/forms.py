"""tasks 表单：工作类型、任务模板、比赛任务、人员分配、审核人授权。"""
from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from .models import (
    CompetitionTask,
    TaskAssignment,
    TaskTemplate,
    WorkType,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# 工作类型
# ---------------------------------------------------------------------------

class WorkTypeForm(forms.ModelForm):
    class Meta:
        model = WorkType
        fields = ["name", "description", "sort_order"]
        widgets = {
            "name": forms.TextInput(attrs={
                "class": "form-control", "placeholder": "例如：推文排版",
            }),
            "description": forms.TextInput(attrs={
                "class": "form-control", "placeholder": "这项工作是做什么的（可选）",
            }),
            "sort_order": forms.NumberInput(attrs={"class": "form-control"}),
        }

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("工作类型名称不能为空。")
        # 同名校验（忽略大小写与首尾空格），避免"摄影"和" 摄影 "并存
        qs = WorkType.objects.filter(name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("已存在同名的工作类型。")
        return name


# ---------------------------------------------------------------------------
# 任务模板
# ---------------------------------------------------------------------------

class TaskTemplateForm(forms.ModelForm):
    """任务模板表单。

    重点是材料要求的合法性：必填 ⇒ 允许。
    这里做表单级校验，模型 clean 里再做一道（供非表单路径使用）。
    """

    MATERIAL_CHOICES = (
        ("none", "不需要"),
        ("optional", "选填"),
        ("required", "必交"),
    )
    MATERIAL_ROWS = (
        ("text", "文字说明", "text_requirement"),
        ("image", "图片证明", "image_requirement"),
        ("file", "文件附件", "file_requirement"),
        ("link", "作品链接", "link_requirement"),
    )

    text_requirement = forms.ChoiceField(
        label="文字说明要求", choices=MATERIAL_CHOICES, required=False,
        widget=forms.RadioSelect(),
    )
    image_requirement = forms.ChoiceField(
        label="图片证明要求", choices=MATERIAL_CHOICES, required=False,
        widget=forms.RadioSelect(),
    )
    file_requirement = forms.ChoiceField(
        label="文件附件要求", choices=MATERIAL_CHOICES, required=False,
        widget=forms.RadioSelect(),
    )
    link_requirement = forms.ChoiceField(
        label="作品链接要求", choices=MATERIAL_CHOICES, required=False,
        widget=forms.RadioSelect(),
    )

    class Meta:
        model = TaskTemplate
        fields = [
            "name", "work_type", "description",
            "allow_text", "require_text",
            "allow_image", "require_image",
            "allow_file", "require_file",
            "allow_link", "require_link",
            "max_records_per_member",
        ]
        widgets = {
            "name": forms.TextInput(attrs={
                "class": "form-control", "placeholder": "例如：赛后宣传推文",
            }),
            "work_type": forms.Select(attrs={"class": "form-select"}),
            "description": forms.Textarea(attrs={
                "class": "form-control", "rows": 3,
                "placeholder": "这个任务的交付要求、注意事项",
            }),
            "max_records_per_member": forms.NumberInput(attrs={
                "class": "form-control", "min": 1, "step": 1,
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 只允许选用「启用中」的工作类型；编辑既有模板时，
        # 其原本关联的工作类型即使已停用也仍显示为当前选中值，
        # 否则用户一打开编辑页就会被强制换掉类型。
        active_qs = WorkType.objects.filter(is_active=True)
        if self.instance.pk and self.instance.work_type_id:
            self.fields["work_type"].queryset = WorkType.objects.filter(
                Q(is_active=True) | Q(pk=self.instance.work_type_id)
            ).distinct()
        else:
            self.fields["work_type"].queryset = active_qs
        for key, _label, field_name in self.MATERIAL_ROWS:
            allow = bool(getattr(self.instance, "allow_%s" % key, False))
            require = bool(getattr(self.instance, "require_%s" % key, False))
            self.fields[field_name].initial = (
                "required" if require else "optional" if allow else "none"
            )
            self.fields[field_name].widget.attrs["class"] = "pp-choice-input"

        self.material_choice_rows = [
            (label, self[field_name])
            for _key, label, field_name in self.MATERIAL_ROWS
        ]

    def clean_max_records_per_member(self):
        count = self.cleaned_data.get("max_records_per_member")
        if count is None or int(count) < 1:
            raise forms.ValidationError("每名干事允许创建的工作记录数量必须是正整数（至少 1）。")
        return count

    def clean(self):
        cleaned = super().clean()
        labels = {
            "text": "文字说明", "image": "图片证明", "file": "文件附件",
            "link": "作品链接",
        }
        for key, label in labels.items():
            mode = ""
            if key in {row[0] for row in self.MATERIAL_ROWS}:
                mode = (self.data.get("%s_requirement" % key) or "").strip()

            if mode in {"none", "optional", "required"}:
                allow = mode != "none"
                require = mode == "required"
                cleaned["allow_%s" % key] = allow
                cleaned["require_%s" % key] = require
            else:
                allow = bool(cleaned.get("allow_%s" % key))
                require = bool(cleaned.get("require_%s" % key))

            if require and not allow:
                self.add_error(
                    "allow_%s" % key,
                    "「%s」被设为必填，必须同时勾选允许提交。" % label,
                )

        return cleaned


# ---------------------------------------------------------------------------
# 比赛任务（从模板创建）
# ---------------------------------------------------------------------------

class CompetitionTaskForm(forms.ModelForm):
    """在比赛下创建任务。

    模板只能从「启用中」里选；创建时会冻结规则快照（见模型 build_from_template）。
    """

    class Meta:
        model = CompetitionTask
        fields = ["template", "name", "description", "deadline"]
        widgets = {
            "template": forms.Select(attrs={"class": "form-select"}),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "deadline": forms.DateTimeInput(attrs={
                "class": "form-control", "type": "datetime-local",
            }, format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, competition=None, **kwargs):
        self.competition = competition
        super().__init__(*args, **kwargs)
        self.fields["deadline"].input_formats = [
            "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
        ]
        self.fields["deadline"].required = False
        # 只允许选启用中的模板
        available = TaskTemplate.objects.filter(is_active=True).select_related("work_type")
        if competition is not None:
            # 该比赛下已经用过同一模板的不再重复提供
            used = CompetitionTask.objects.filter(competition=competition).values_list(
                "template_id", flat=True
            )
            available = available.exclude(pk__in=list(used))
        self.fields["template"].queryset = available
        self.fields["template"].empty_label = "— 请选择任务模板 —"

    def clean_template(self):
        template = self.cleaned_data.get("template")
        if template is None:
            raise forms.ValidationError("请选择一个任务模板。")
        if not template.is_active:
            raise forms.ValidationError("该模板已停用，不能用于新任务。")
        if self.competition is not None:
            if CompetitionTask.objects.filter(
                competition=self.competition, template=template
            ).exists():
                raise forms.ValidationError("本场比赛已经创建过该模板的任务。")
        return template

    def clean(self):
        cleaned = super().clean()
        deadline = cleaned.get("deadline")
        competition = self.competition
        if deadline and competition:
            if deadline.date() < competition.event_date:
                self.add_error(
                    "deadline",
                    "任务截止时间不能早于比赛日期，请检查是否填错。",
                )
        return cleaned


class TaskEditForm(forms.ModelForm):
    """修改比赛任务。

    刻意**不包含**材料规则字段：规则在任务创建时已冻结为快照，
    这里改动会让已分配干事的任务要求悄悄变化。
    如需调整规则，正确做法是停用旧任务、用新模板建一个新任务。
    """

    class Meta:
        model = CompetitionTask
        fields = ["name", "description", "deadline"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "deadline": forms.DateTimeInput(attrs={
                "class": "form-control", "type": "datetime-local",
            }, format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["deadline"].input_formats = [
            "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
        ]
        self.fields["deadline"].required = False
        self.fields["deadline"].help_text = (
            "留空表示继承比赛默认截止时间（%s）"
            % (self.instance.competition.default_deadline
               if self.instance and self.instance.pk else "—")
        )

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("任务名称不能为空。")
        return name

    def clean_deadline(self):
        deadline = self.cleaned_data.get("deadline")
        competition = self.instance.competition if self.instance else None
        if deadline and competition and deadline.date() < competition.event_date:
            raise forms.ValidationError("任务截止时间不能早于比赛日期，请检查是否填错。")
        return deadline


# ---------------------------------------------------------------------------
# 人员分配
# ---------------------------------------------------------------------------

class TaskAssignmentForm(forms.Form):
    """为某个任务分配干事（支持一次选多人）。"""

    members = forms.ModelMultipleChoiceField(
        label="选择干事",
        queryset=User.objects.none(),
        widget=forms.SelectMultiple(attrs={"class": "form-select", "size": 10}),
        help_text="可按住 Ctrl（Mac 为 Command）多选；已分配过的干事不会重复出现",
    )

    def __init__(self, *args, task=None, **kwargs):
        self.task = task
        super().__init__(*args, **kwargs)

        from core.permissions import ROLE_MEMBER

        # 候选：启用中的账号，且拥有 member 角色
        candidates = (
            User.objects
            .filter(is_active=True, roles__code=ROLE_MEMBER, roles__is_active=True)
            .distinct()
            .order_by("account")
        )
        if task is not None:
            already = TaskAssignment.objects.filter(
                task=task, is_active=True
            ).values_list("member_id", flat=True)
            candidates = candidates.exclude(pk__in=list(already))
        self.fields["members"].queryset = candidates
        self.fields["members"].label_from_instance = \
            lambda u: "%s（%s）" % (u.real_name, u.account)

    def clean_members(self):
        members = self.cleaned_data.get("members")
        if not members:
            raise forms.ValidationError("请至少选择一名干事。")
        return members


# ---------------------------------------------------------------------------
# 审核人授权
# ---------------------------------------------------------------------------

class ReviewerAssignmentForm(forms.Form):
    """为比赛添加审核人授权（支持一次选多人）。"""

    reviewers = forms.ModelMultipleChoiceField(
        label="选择审核人",
        queryset=User.objects.none(),
        widget=forms.SelectMultiple(attrs={"class": "form-select", "size": 10}),
        help_text="审核权需要逐场授权；管理员身份本身不自动获得审核权",
    )

    def __init__(self, *args, competition=None, **kwargs):
        self.competition = competition
        super().__init__(*args, **kwargs)

        from competitions.models import ReviewerAssignment

        candidates = User.objects.filter(is_active=True).order_by("account")
        if competition is not None:
            already = ReviewerAssignment.objects.filter(
                competition=competition, is_active=True
            ).values_list("reviewer_id", flat=True)
            candidates = candidates.exclude(pk__in=list(already))
        self.fields["reviewers"].queryset = candidates
        self.fields["reviewers"].label_from_instance = \
            lambda u: "%s（%s）" % (u.real_name, u.account)

    def clean_reviewers(self):
        reviewers = self.cleaned_data.get("reviewers")
        if not reviewers:
            raise forms.ValidationError("请至少选择一名审核人。")
        return reviewers
