"""Focused regression tests for service-boundary business rules."""

from types import SimpleNamespace

import pytest

from submissions.services import (
    PendingUpload,
    SubmissionError,
    assert_material_requirements,
)


def _task(**snapshot):
    defaults = {
        "allow_text": False,
        "allow_image": False,
        "allow_file": False,
        "allow_link": False,
    }
    defaults.update(snapshot)
    return SimpleNamespace(rule_snapshot=defaults)


def test_service_rejects_disallowed_text():
    with pytest.raises(SubmissionError, match="文字说明"):
        assert_material_requirements(_task(), [], "不应允许的文字")


def test_service_rejects_disallowed_image():
    image = PendingUpload(material_type="image", storage_key="test.jpg")
    with pytest.raises(SubmissionError, match="图片证明"):
        assert_material_requirements(_task(), [image], "")
