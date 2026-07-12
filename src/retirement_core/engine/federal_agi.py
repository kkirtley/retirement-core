from __future__ import annotations

from decimal import Decimal

from retirement_core.domain.enums import (
    AccountType,
    CharitableGivingMethod,
    FederalAgiComponentType,
    IncomeType,
    TransactionType,
)
from retirement_core.domain.models import (
    AnnualSocialSecurityBenefit,
    AnnualTransactionInput,
    ProjectionRequest,
    ResolvedAnnualIncome,
    SocialSecurityTaxationResult,
    TransactionLedgerEntry,
)
from retirement_core.domain.tax import AnnualFederalAgiResult, FederalAgiComponentResult

_PRETAX_ACCOUNT_TYPES = {AccountType.TRADITIONAL_IRA, AccountType.TRADITIONAL_401K}


def build_annual_federal_agi(
    request: ProjectionRequest,
    year: int,
    year_entries: list[TransactionLedgerEntry],
    plan_transactions: list[AnnualTransactionInput],
    social_security_benefits: list[AnnualSocialSecurityBenefit],
    social_security_taxation: SocialSecurityTaxationResult | None,
    resolved_income: list[ResolvedAnnualIncome],
) -> AnnualFederalAgiResult:
    accounts = {account.id: account for account in request.plan.accounts}
    people = {person.id for person in request.plan.people}
    components: list[FederalAgiComponentResult] = []
    diagnostics: list[str] = []

    taxable_pension = Decimal("0")
    taxable_wages = Decimal("0")
    taxable_interest = Decimal("0")
    tax_exempt_interest = Decimal("0")

    resolved_by_id = {resolved.income_id: resolved for resolved in resolved_income}
    for income in request.plan.income:
        resolved = resolved_by_id.get(income.id)
        if resolved is None:
            continue
        if income.owner_id is not None and people and income.owner_id not in people:
            raise ValueError(f"Income {income.id} references unknown owner {income.owner_id}")
        source_transaction_id = f"income:{income.id}:{year}"
        match income.income_type:
            case IncomeType.PENSION:
                _require_owner_for_agi_income(income.id, income.owner_id, people)
                if income.taxable_federal:
                    taxable_pension += resolved.taxable_amount
                    components.append(
                        _component(
                            FederalAgiComponentType.TAXABLE_PENSION,
                            resolved.taxable_amount,
                            owner_id=income.owner_id,
                            included_in_federal_agi=True,
                            included_in_irmaa_magi=True,
                            source_transaction_ids=(source_transaction_id,),
                            provenance=f"scheduled income {income.id}: taxable federal pension",
                        )
                    )
            case IncomeType.TAXABLE_INTEREST:
                _require_owner_for_agi_income(income.id, income.owner_id, people)
                taxable_interest += resolved.taxable_amount
                components.append(
                    _component(
                        FederalAgiComponentType.TAXABLE_INTEREST,
                        resolved.taxable_amount,
                        owner_id=income.owner_id,
                        included_in_federal_agi=True,
                        included_in_irmaa_magi=True,
                        source_transaction_ids=(source_transaction_id,),
                        provenance=f"scheduled income {income.id}: taxable interest",
                    )
                )
            case IncomeType.TAX_EXEMPT_INTEREST:
                _require_owner_for_agi_income(income.id, income.owner_id, people)
                tax_exempt_interest += resolved.spendable_cash_amount
                components.append(
                    _component(
                        FederalAgiComponentType.TAX_EXEMPT_INTEREST,
                        resolved.spendable_cash_amount,
                        owner_id=income.owner_id,
                        included_in_federal_agi=False,
                        included_in_irmaa_magi=True,
                        source_transaction_ids=(source_transaction_id,),
                        provenance=f"scheduled income {income.id}: tax-exempt interest",
                    )
                )
            case IncomeType.W2_WAGES:
                _require_owner_for_agi_income(income.id, income.owner_id, people)
                taxable_wages += resolved.taxable_amount
                components.append(
                    _component(
                        FederalAgiComponentType.TAXABLE_WAGES,
                        resolved.taxable_amount,
                        owner_id=income.owner_id,
                        included_in_federal_agi=True,
                        included_in_irmaa_magi=True,
                        source_transaction_ids=(source_transaction_id,),
                        provenance=f"scheduled income {income.id}: W-2 wages",
                    )
                )
            case IncomeType.VA_DISABILITY:
                components.append(
                    _component(
                        FederalAgiComponentType.OTHER_SUPPORTED_AGI,
                        resolved.spendable_cash_amount,
                        owner_id=income.owner_id,
                        included_in_federal_agi=False,
                        included_in_irmaa_magi=False,
                        source_transaction_ids=(source_transaction_id,),
                        provenance=f"scheduled income {income.id}: VA disability excluded from AGI",
                    )
                )
            case _:
                diagnostic = (
                    f"Federal AGI treatment is unsupported for income {income.id} "
                    f"of type {income.income_type.value}"
                )
                diagnostics.append(diagnostic)
                raise ValueError(diagnostic)

    taxable_rmd = Decimal("0")
    taxable_non_rmd_ira = Decimal("0")
    taxable_roth_conversions = Decimal("0")
    qcd = Decimal("0")
    for entry in year_entries:
        source_account = accounts.get(entry.source_account_id or "")
        match entry.transaction_type:
            case TransactionType.RMD_DISTRIBUTION:
                taxable_rmd += entry.taxable_ordinary_income
                components.append(
                    _component(
                        FederalAgiComponentType.TAXABLE_RMD_DISTRIBUTION,
                        entry.taxable_ordinary_income,
                        owner_id=source_account.owner_id if source_account else None,
                        included_in_federal_agi=True,
                        included_in_irmaa_magi=True,
                        source_account_id=entry.source_account_id,
                        source_transaction_ids=(entry.transaction_id,),
                        provenance="generated taxable RMD distribution",
                    )
                )
            case TransactionType.WITHDRAWAL:
                if (
                    source_account is not None
                    and source_account.account_type in _PRETAX_ACCOUNT_TYPES
                ):
                    taxable_non_rmd_ira += entry.taxable_ordinary_income
                    components.append(
                        _component(
                            FederalAgiComponentType.TAXABLE_NON_RMD_IRA_DISTRIBUTION,
                            entry.taxable_ordinary_income,
                            owner_id=source_account.owner_id,
                            included_in_federal_agi=True,
                            included_in_irmaa_magi=True,
                            source_account_id=entry.source_account_id,
                            source_transaction_ids=(entry.transaction_id,),
                            provenance="taxable non-RMD IRA distribution",
                        )
                    )
            case TransactionType.ROTH_CONVERSION:
                amount = entry.taxable_amount if entry.taxable_amount is not None else entry.amount
                taxable_roth_conversions += amount
                components.append(
                    _component(
                        FederalAgiComponentType.FEDERALLY_TAXABLE_ROTH_CONVERSION,
                        amount,
                        owner_id=source_account.owner_id if source_account else None,
                        included_in_federal_agi=True,
                        included_in_irmaa_magi=True,
                        source_account_id=entry.source_account_id,
                        source_transaction_ids=(entry.transaction_id,),
                        provenance="federally taxable Roth conversion amount",
                    )
                )
            case TransactionType.CHARITABLE_GIVING:
                if entry.charitable_method is CharitableGivingMethod.QCD:
                    qcd += entry.amount
                    components.append(
                        _component(
                            FederalAgiComponentType.QCD,
                            entry.amount,
                            owner_id=source_account.owner_id if source_account else None,
                            included_in_federal_agi=False,
                            included_in_irmaa_magi=False,
                            source_account_id=entry.source_account_id,
                            source_transaction_ids=(entry.transaction_id,),
                            provenance="QCD excluded from federal AGI and IRMAA MAGI",
                        )
                    )

    for transaction in plan_transactions:
        if transaction.transaction_type is not TransactionType.TRANSFER:
            continue
        source = accounts.get(transaction.source_account_id or "")
        destination = accounts.get(transaction.destination_account_id or "")
        if (
            source is not None
            and destination is not None
            and source.account_type in _PRETAX_ACCOUNT_TYPES
            and destination.account_type in _PRETAX_ACCOUNT_TYPES
        ):
            components.append(
                _component(
                    FederalAgiComponentType.PRETAX_ROLLOVER,
                    transaction.amount,
                    owner_id=source.owner_id,
                    included_in_federal_agi=False,
                    included_in_irmaa_magi=False,
                    source_account_id=transaction.source_account_id,
                    source_transaction_ids=(transaction.id,),
                    provenance="pretax-to-pretax rollover excluded from federal AGI and IRMAA MAGI",
                )
            )

    taxable_social_security = (
        social_security_taxation.taxable_social_security
        if social_security_taxation is not None
        else Decimal("0")
    )
    if taxable_social_security > 0:
        components.append(
            _component(
                FederalAgiComponentType.FEDERALLY_TAXABLE_SOCIAL_SECURITY,
                taxable_social_security,
                included_in_federal_agi=True,
                included_in_irmaa_magi=True,
                source_transaction_ids=tuple(
                    f"social-security:{benefit.source_id}:{year}"
                    for benefit in social_security_benefits
                ),
                provenance="household-level taxable Social Security calculation",
            )
        )

    return AnnualFederalAgiResult(
        tax_year=year,
        filing_status=request.plan.filing_status,
        taxable_wages=taxable_wages,
        taxable_pension=taxable_pension,
        taxable_rmd_distributions=taxable_rmd,
        taxable_non_rmd_ira_distributions=taxable_non_rmd_ira,
        federally_taxable_roth_conversions=taxable_roth_conversions,
        federally_taxable_social_security=taxable_social_security,
        taxable_interest=taxable_interest,
        tax_exempt_interest=tax_exempt_interest,
        components=tuple(components),
        unsupported_income_diagnostics=tuple(diagnostics),
    )


def supported_federal_ordinary_income(agi: AnnualFederalAgiResult) -> Decimal:
    return (
        agi.taxable_wages
        + agi.taxable_pension
        + agi.taxable_rmd_distributions
        + agi.taxable_non_rmd_ira_distributions
        + agi.federally_taxable_roth_conversions
        + agi.federally_taxable_social_security
        + agi.taxable_interest
    )


def supported_federal_ordinary_income_before_social_security(
    agi: AnnualFederalAgiResult,
) -> Decimal:
    return supported_federal_ordinary_income(agi) - agi.federally_taxable_social_security


def supported_provisional_income_before_social_security(
    agi: AnnualFederalAgiResult,
) -> Decimal:
    """Currently supported other income for Social Security provisional income."""
    return supported_federal_ordinary_income_before_social_security(agi) + agi.tax_exempt_interest


def _component(
    component_type: FederalAgiComponentType,
    amount: Decimal,
    *,
    owner_id: str | None = None,
    included_in_federal_agi: bool,
    included_in_irmaa_magi: bool,
    source_account_id: str | None = None,
    source_transaction_ids: tuple[str, ...] = (),
    provenance: str,
) -> FederalAgiComponentResult:
    return FederalAgiComponentResult(
        component_type=component_type,
        owner_id=owner_id,
        amount=amount,
        included_in_federal_agi=included_in_federal_agi,
        included_in_irmaa_magi=included_in_irmaa_magi,
        source_account_id=source_account_id,
        source_transaction_ids=source_transaction_ids,
        provenance=provenance,
    )


def _active_in_year(start_year: int, end_year: int | None, year: int) -> bool:
    return start_year <= year and (end_year is None or end_year >= year)


def _require_owner_for_agi_income(
    income_id: str,
    owner_id: str | None,
    people: set[str],
) -> None:
    if people and owner_id is None:
        raise ValueError(f"Federal AGI income {income_id} requires an owner_id")
