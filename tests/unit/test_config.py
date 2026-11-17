from __future__ import annotations

import pytest
from pydantic import ValidationError

from verityflow.config import Config, Limits


def test_defaults() -> None:
    config = Config()
    assert config.limits.max_cells == 50_000
    assert config.limits.max_candidate_policies == 8
    assert config.approvals.authorized_roles == ("policy_owner",)


def test_config_is_frozen() -> None:
    config = Config()
    with pytest.raises(ValidationError):
        config.limits.max_cells = 1


def test_limits_reject_non_positive_values() -> None:
    with pytest.raises(ValidationError):
        Limits(max_cells=0)
