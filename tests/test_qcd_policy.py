from decimal import Decimal

import pytest
from pydantic import ValidationError

from retirement_core.domain.enums import QcdTargetMode
from retirement_core.domain.models import AnnualQcdOverride, GivingPolicyInput, QcdPolicyInput


@pytest.mark.parametrize(
    ("legacy_value", "enabled"),
    [(False, False), (True, True)],
)
def test_legacy_qcd_enabled_maps_without_inventing_a_target(
    legacy_value: bool, enabled: bool
) -> None:
    policy = GivingPolicyInput.model_validate({"qcd_enabled": legacy_value})

    assert policy.qcd_policy.enabled is enabled
    assert policy.qcd_policy.target_mode is QcdTargetMode.NONE
    assert policy.qcd_policy.annual_qcd_floor == Decimal("0.00")


def test_conflicting_legacy_and_nested_qcd_policy_fails() -> None:
    with pytest.raises(ValidationError, match="conflicts"):
        GivingPolicyInput.model_validate({"qcd_enabled": True, "qcd_policy": {"enabled": False}})


def test_matching_legacy_and_typed_nested_policy_is_accepted() -> None:
    policy = GivingPolicyInput(
        qcd_enabled=True,
        qcd_policy=QcdPolicyInput(enabled=True),
    )

    assert policy.qcd_policy.enabled is True


def test_qcd_policy_mutable_fields_are_not_shared() -> None:
    first = QcdPolicyInput()
    second = QcdPolicyInput()

    first.owner_priority.append("owner-a")
    first.account_priority.append("ira-a")
    first.paused_years.add(2030)
    first.annual_overrides[2031] = AnnualQcdOverride(paused=True)

    assert second.owner_priority == []
    assert second.account_priority == []
    assert second.paused_years == set()
    assert second.annual_overrides == {}
