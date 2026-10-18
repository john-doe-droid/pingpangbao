"""competitions 表单。"""
from django import forms

from .models import Competition, CompetitionStatus


class CompetitionForm(forms.ModelForm):
    """比赛创建 / 修改表单。

    注意：status 不在这里直接编辑 —— 归档必须走专门的归档动作
    （有额外确认与操作记录），避免顺手把状态改掉。
    """

    class Meta:
        model = Competition
        fields = ["name", "description", "event_date", "default_deadline"]
        # 统一中文错误提示，避免露出 Django 默认的"这个字段是必填项"
        error_messages = {
            "name": {
                "required": "比赛名称不能为空。",
                "blank": "比赛名称不能为空。",
            },
            "event_date": {
                "required": "请填写比赛日期。",
                "invalid": "比赛日期格式不正确。",
            },
            "default_deadline": {
                "required": "请填写默认提交截止时间。",
                "invalid": "截止时间格式不正确。",
            },
        }
        widgets = {
            "name": forms.TextInput(attrs={
                "class": "form-control", "placeholder": "例如：2026 秋季新生杯",
            }),
            "description": forms.Textarea(attrs={
                "class": "form-control", "rows": 3,
                "placeholder": "这站比赛是做什么的、有哪些注意事项",
            }),
            "event_date": forms.DateInput(attrs={
                "class": "form-control", "type": "date",
            }),
            "default_deadline": forms.DateTimeInput(attrs={
                "class": "form-control", "type": "datetime-local",
            }, format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # datetime-local 提交的是 "2026-10-20T18:00"，必须指定输入格式，
        # 否则 Django 会按默认格式解析并报「输入无效的日期/时间」。
        self.fields["default_deadline"].input_formats = [
            "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
        ]
        self.fields["event_date"].input_formats = ["%Y-%m-%d"]

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("比赛名称不能为空。")
        return name

    def clean(self):
        cleaned = super().clean()
        event_date = cleaned.get("event_date")
        deadline = cleaned.get("default_deadline")
        # 截止时间早于比赛日期在业务上说不通（比赛还没办就截止了）
        if event_date and deadline and deadline.date() < event_date:
            self.add_error(
                "default_deadline",
                "默认提交截止时间不能早于比赛日期，请检查是否填错。",
            )
        return cleaned


class ArchiveCompetitionForm(forms.Form):
    """归档确认表单。

    刻意要求输入比赛名称来确认 —— 归档会使该比赛停止接收新工作记录，
    属于需要"想清楚再点"的操作。
    """

    confirm_name = forms.CharField(
        label="请输入比赛名称以确认归档",
        widget=forms.TextInput(attrs={
            "class": "form-control", "autocomplete": "off",
            "placeholder": "原样输入比赛名称",
        }),
    )

    def __init__(self, *args, competition=None, **kwargs):
        self.competition = competition
        super().__init__(*args, **kwargs)

    def clean_confirm_name(self):
        value = (self.cleaned_data.get("confirm_name") or "").strip()
        if self.competition and value != self.competition.name:
            raise forms.ValidationError("输入的比赛名称与当前比赛不一致，未执行归档。")
        return value
