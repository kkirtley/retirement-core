from decimal import Decimal

import pytest
from pydantic import ValidationError

from retirement_core.domain.models import ProjectionRequest
from retirement_core.engine.projection import _resolve_annual_income, run_projection


def _plan(
    income: dict[str, object] | list[dict[str, object]],
    start_date: str = "2026-01-01",
    end_date: str = "2026-12-31",
) -> ProjectionRequest:
    return ProjectionRequest.model_validate(
        {
            "plan": {
                "household_name": "Synthetic",
                "filing_status": "married_filing_jointly",
                "start_date": start_date,
                "end_date": end_date,
                "people": [{"id": "owner", "name": "Owner", "date_of_birth": "1970-01-01"}],
                "accounts": [
                    {
                        "id": "cash",
                        "owner_id": "owner",
                        "account_type": "cash",
                        "starting_balance": "0",
                    }
                ],
                "income": income if isinstance(income, list) else [income],
            }
        }
    )


def _self_employment(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "business",
        "income_type": "self_employment_net_income",
        "owner_id": "owner",
        "annual_taxable_amount": "1000",
        "annual_spendable_cash_amount": "700",
        "start_date": "2026-01-01",
        "destination_account_id": "cash",
        "self_employment_tax_base": {"business_id": "business-a", "net_business_profit": "1000"},
    }
    value.update(updates)
    return value


def test_self_employment_resolves_typed_base_then_projection_fails_closed() -> None:
    request = _plan(_self_employment())
    resolved = _resolve_annual_income(request, 2026)[0]
    assert resolved.self_employment_tax_base is not None
    assert resolved.self_employment_tax_base.net_business_profit == Decimal("1000")
    assert resolved.taxable_amount == Decimal("1000")
    with pytest.raises(ValueError, match="projection integration is not implemented"):
        run_projection(request)


def test_self_employment_override_is_authoritative() -> None:
    request = _plan(
        _self_employment(
            annual_overrides={
                2026: {
                    "taxable_amount": "1200",
                    "spendable_cash_amount": "300",
                    "self_employment_tax_base": {
                        "business_id": "business-a",
                        "net_business_profit": "1200",
                    },
                }
            }
        )
    )
    resolved = _resolve_annual_income(request, 2026)[0]
    assert resolved.taxable_amount == Decimal("1200")
    assert resolved.spendable_cash_amount == Decimal("300")


def test_invalid_tax_base_attachments_and_mismatch_fail() -> None:
    with pytest.raises(ValidationError, match="must equal"):
        _plan(_self_employment(annual_taxable_amount="999"))
    with pytest.raises(ValidationError, match="only valid"):
        _plan(
            {
                "id": "pension",
                "income_type": "pension",
                "annual_amount": "1",
                "start_date": "2026-01-01",
                "destination_account_id": "cash",
                "self_employment_tax_base": {"business_id": "x", "net_business_profit": "1"},
            }
        )


def test_w2_payroll_bases_remain_distinct_and_overrideable() -> None:
    request = _plan(
        {
            "id": "wages",
            "income_type": "w2_wages",
            "owner_id": "owner",
            "annual_taxable_amount": "1000",
            "annual_spendable_cash_amount": "800",
            "start_date": "2026-01-01",
            "destination_account_id": "cash",
            "w2_payroll_tax_bases": {"social_security_wages": "900", "medicare_wages": "950"},
            "annual_overrides": {
                2026: {
                    "taxable_amount": "1100",
                    "spendable_cash_amount": "850",
                    "w2_payroll_tax_bases": {
                        "social_security_wages": "1000",
                        "medicare_wages": "1050",
                    },
                }
            },
        }
    )
    resolved = _resolve_annual_income(request, 2026)[0]
    assert resolved.w2_payroll_tax_bases is not None
    assert resolved.w2_payroll_tax_bases.social_security_wages == Decimal("1000")
    assert resolved.taxable_amount == Decimal("1100")


def test_w2_bases_are_required_before_self_employment_safety_gate() -> None:
    request = _plan(
        [
            _self_employment(),
            {
                "id": "wages",
                "income_type": "w2_wages",
                "owner_id": "owner",
                "annual_taxable_amount": "100",
                "annual_spendable_cash_amount": "100",
                "start_date": "2026-01-01",
                "destination_account_id": "cash",
            },
        ]
    )
    with pytest.raises(ValueError, match="W-2 source wages requires payroll tax bases for 2026"):
        run_projection(request)


def test_one_business_per_owner_and_override_base_selection_fail_closed() -> None:
    with pytest.raises(ValidationError, match="Only one self-employment business"):
        _plan(
            [
                _self_employment(),
                _self_employment(
                    id="other",
                    self_employment_tax_base={
                        "business_id": "other",
                        "net_business_profit": "1000",
                    },
                ),
            ]
        )


def test_self_employment_requires_known_owner_and_cash_destination() -> None:
    with pytest.raises(ValidationError, match="unknown owner"):
        _plan(_self_employment(owner_id="unknown"))
    with pytest.raises(ValidationError, match="existing cash destination account"):
        request_data = _self_employment(destination_account_id="traditional")
        ProjectionRequest.model_validate(
            {
                "plan": {
                    "household_name": "Synthetic",
                    "filing_status": "married_filing_jointly",
                    "start_date": "2026-01-01",
                    "end_date": "2026-12-31",
                    "people": [{"id": "owner", "name": "Owner", "date_of_birth": "1970-01-01"}],
                    "accounts": [
                        {
                            "id": "cash",
                            "owner_id": "owner",
                            "account_type": "cash",
                            "starting_balance": "0",
                        },
                        {
                            "id": "traditional",
                            "owner_id": "owner",
                            "account_type": "traditional_ira",
                            "starting_balance": "0",
                        },
                    ],
                    "income": [request_data],
                }
            }
        )


def test_preexisting_partial_year_sources_require_tax_base_overrides() -> None:
    with pytest.raises(ValidationError, match="annual override for partial"):
        _plan(_self_employment(), "2026-07-01", "2026-12-31")


def test_partial_year_w2_bases_are_required_only_when_self_employment_is_active() -> None:
    wages = {
        "id": "wages",
        "income_type": "w2_wages",
        "owner_id": "owner",
        "annual_taxable_amount": "100",
        "annual_spendable_cash_amount": "100",
        "start_date": "2026-01-01",
        "destination_account_id": "cash",
        "w2_payroll_tax_bases": {"social_security_wages": "100", "medicare_wages": "100"},
        "annual_overrides": {2026: {"taxable_amount": "50", "spendable_cash_amount": "50"}},
    }
    _plan(wages, "2026-07-01", "2026-12-31")
    request = _plan(
        [
            _self_employment(
                annual_overrides={
                    2026: {
                        "taxable_amount": "1000",
                        "spendable_cash_amount": "500",
                        "self_employment_tax_base": {
                            "business_id": "business-a",
                            "net_business_profit": "1000",
                        },
                    }
                }
            ),
            wages,
        ],
        "2026-07-01",
        "2026-12-31",
    )
    with pytest.raises(ValueError, match="W-2 source wages requires payroll tax bases for 2026"):
        run_projection(request)


def test_complete_partial_year_w2_and_self_employment_overrides_reach_safety_gate() -> None:
    request = _plan(
        [
            _self_employment(
                annual_overrides={
                    2026: {
                        "taxable_amount": "1000",
                        "spendable_cash_amount": "500",
                        "self_employment_tax_base": {
                            "business_id": "business-a",
                            "net_business_profit": "1000",
                        },
                    }
                }
            ),
            {
                "id": "wages",
                "income_type": "w2_wages",
                "owner_id": "owner",
                "annual_taxable_amount": "100",
                "annual_spendable_cash_amount": "100",
                "start_date": "2026-01-01",
                "destination_account_id": "cash",
                "w2_payroll_tax_bases": {
                    "social_security_wages": "100",
                    "medicare_wages": "100",
                },
                "annual_overrides": {
                    2026: {
                        "taxable_amount": "50",
                        "spendable_cash_amount": "50",
                        "w2_payroll_tax_bases": {
                            "social_security_wages": "100",
                            "medicare_wages": "100",
                        },
                    }
                },
            },
        ],
        "2026-07-01",
        "2026-12-31",
    )
    with pytest.raises(ValueError, match="projection integration is not implemented"):
        run_projection(request)


def test_partial_year_override_is_exact_and_midyear_source_is_not_preexisting() -> None:
    request = _plan(
        _self_employment(
            annual_overrides={
                2026: {
                    "taxable_amount": "1200",
                    "spendable_cash_amount": "345",
                    "self_employment_tax_base": {
                        "business_id": "business-a",
                        "net_business_profit": "1200",
                    },
                }
            }
        ),
        "2026-07-01",
        "2026-12-31",
    )
    resolved = _resolve_annual_income(request, 2026)[0]
    assert resolved.taxable_amount == Decimal("1200")
    assert resolved.spendable_cash_amount == Decimal("345")
    with pytest.raises(ValueError, match="projection integration is not implemented"):
        run_projection(request)

    starts_midyear = _plan(_self_employment(start_date="2026-08-01"), "2026-07-01", "2026-12-31")
    assert _resolve_annual_income(starts_midyear, 2026)[0].taxable_amount == Decimal("1000")
