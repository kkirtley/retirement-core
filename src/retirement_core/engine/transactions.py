from dataclasses import dataclass
from decimal import Decimal

from retirement_core.domain.enums import (
    AccountType,
    CharitableGivingMethod,
    TransactionType,
)
from retirement_core.domain.models import (
    AccountInput,
    AnnualTransactionInput,
    TransactionLedgerEntry,
)


@dataclass
class AccountActivity:
    contributions: Decimal = Decimal("0")
    transfers_in: Decimal = Decimal("0")
    withdrawals: Decimal = Decimal("0")
    transfers_out: Decimal = Decimal("0")
    roth_conversions: Decimal = Decimal("0")
    qcd: Decimal = Decimal("0")


_TRADITIONAL_TYPES = {AccountType.TRADITIONAL_IRA, AccountType.TRADITIONAL_401K}
_ROTH_TYPES = {AccountType.ROTH_IRA, AccountType.ROTH_401K}


def apply_transaction(
    transaction: AnnualTransactionInput,
    accounts: dict[str, AccountInput],
    balances: dict[str, Decimal],
    activity: dict[str, AccountActivity],
    *,
    allow_negative_cash_balance: bool,
) -> TransactionLedgerEntry:
    source = _account(transaction.source_account_id, accounts, "source")
    destination = _account(transaction.destination_account_id, accounts, "destination")
    amount = transaction.amount
    balance_changes: dict[str, Decimal] = {}

    def debit(account: AccountInput) -> None:
        balance_changes[account.id] = balance_changes.get(account.id, Decimal("0")) - amount

    def credit(account: AccountInput) -> None:
        balance_changes[account.id] = balance_changes.get(account.id, Decimal("0")) + amount

    spendable_income = Decimal("0")
    cash_withdrawal = Decimal("0")
    spending = Decimal("0")
    contribution = Decimal("0")
    federal_tax_payment = Decimal("0")
    federal_tax_refund = Decimal("0")
    federal_income_tax_withholding = Decimal("0")
    missouri_tax_payment = Decimal("0")
    missouri_tax_refund = Decimal("0")
    state_income_tax_withholding = Decimal("0")
    medicare_payment = Decimal("0")
    taxable_ordinary_income = Decimal("0")
    charitable_method = transaction.charitable_method

    match transaction.transaction_type:
        case TransactionType.INCOME | TransactionType.SOCIAL_SECURITY_INCOME:
            _require_absent(source, "Income cannot have a source account")
            destination = _require_type(
                destination, {AccountType.CASH}, "Income destination must be cash"
            )
            credit(destination)
            activity[destination.id].contributions += amount
            spendable_income = amount
            federal_income_tax_withholding = transaction.federal_income_tax_withholding
            state_income_tax_withholding = transaction.state_income_tax_withholding
        case TransactionType.SPENDING:
            _require_absent(destination, "Spending cannot have a destination account")
            source = _require_type(source, {AccountType.CASH}, "Spending source must be cash")
            debit(source)
            activity[source.id].withdrawals += amount
            spending = amount
        case TransactionType.CONTRIBUTION:
            source = _require_type(source, {AccountType.CASH}, "Contribution source must be cash")
            destination = _require_present(
                destination, "Contribution requires a destination account"
            )
            _require_distinct(source, destination)
            debit(source)
            credit(destination)
            activity[source.id].withdrawals += amount
            activity[destination.id].contributions += amount
            contribution = amount
        case TransactionType.WITHDRAWAL:
            source = _require_present(source, "Withdrawal requires a source account")
            destination = _require_type(
                destination, {AccountType.CASH}, "Withdrawal destination must be cash"
            )
            _require_distinct(source, destination)
            debit(source)
            credit(destination)
            activity[source.id].withdrawals += amount
            activity[destination.id].transfers_in += amount
            cash_withdrawal = amount
        case TransactionType.TRANSFER:
            source = _require_present(source, "Transfer requires a source account")
            destination = _require_present(destination, "Transfer requires a destination account")
            _require_distinct(source, destination)
            debit(source)
            credit(destination)
            activity[source.id].transfers_out += amount
            activity[destination.id].transfers_in += amount
        case TransactionType.ROTH_CONVERSION:
            source = _require_type(
                source, _TRADITIONAL_TYPES, "Roth conversion source must be Traditional"
            )
            destination = _require_type(
                destination, _ROTH_TYPES, "Roth conversion destination must be Roth"
            )
            debit(source)
            credit(destination)
            activity[source.id].withdrawals += amount
            activity[source.id].roth_conversions += amount
            activity[destination.id].transfers_in += amount
        case TransactionType.CHARITABLE_GIVING:
            charitable_method = transaction.charitable_method or CharitableGivingMethod.CASH
            if charitable_method is CharitableGivingMethod.QCD:
                _require_absent(destination, "QCD cannot have a destination account")
                source = _require_present(source, "QCD requires a source account")
                debit(source)
                activity[source.id].withdrawals += amount
                activity[source.id].qcd += amount
            else:
                _require_absent(
                    destination, "Cash charitable giving cannot have a destination account"
                )
                source = _require_type(
                    source,
                    {AccountType.CASH, AccountType.TAXABLE},
                    "Cash charitable giving source must be cash or taxable",
                )
                debit(source)
                activity[source.id].withdrawals += amount
                spending = amount
        case TransactionType.RMD_DISTRIBUTION:
            source = _require_present(source, "RMD distribution requires a source account")
            destination = _require_type(
                destination, {AccountType.CASH}, "RMD distribution destination must be cash"
            )
            _require_distinct(source, destination)
            debit(source)
            credit(destination)
            activity[source.id].withdrawals += amount
            activity[destination.id].transfers_in += amount
            cash_withdrawal = amount
            taxable_ordinary_income = amount
        case TransactionType.FEDERAL_TAX_PAYMENT:
            _require_absent(destination, "Federal tax payment cannot have a destination account")
            source = _require_type(
                source, {AccountType.CASH}, "Federal tax payment source must be cash"
            )
            debit(source)
            activity[source.id].withdrawals += amount
            federal_tax_payment = amount
        case TransactionType.FEDERAL_TAX_REFUND:
            _require_absent(source, "Federal tax refund cannot have a source account")
            destination = _require_type(
                destination, {AccountType.CASH}, "Federal tax refund destination must be cash"
            )
            credit(destination)
            activity[destination.id].contributions += amount
            federal_tax_refund = amount
        case TransactionType.MISSOURI_TAX_PAYMENT:
            _require_absent(destination, "Missouri tax payment cannot have a destination account")
            source = _require_type(
                source, {AccountType.CASH}, "Missouri tax payment source must be cash"
            )
            debit(source)
            activity[source.id].withdrawals += amount
            missouri_tax_payment = amount
        case TransactionType.MISSOURI_TAX_REFUND:
            _require_absent(source, "Missouri tax refund cannot have a source account")
            destination = _require_type(
                destination, {AccountType.CASH}, "Missouri tax refund destination must be cash"
            )
            credit(destination)
            activity[destination.id].contributions += amount
            missouri_tax_refund = amount
        case TransactionType.MEDICARE_PAYMENT:
            _require_absent(destination, "Medicare payment cannot have a destination account")
            source = _require_type(
                source, {AccountType.CASH}, "Medicare payment source must be cash"
            )
            debit(source)
            activity[source.id].withdrawals += amount
            medicare_payment = amount

    for account_id, change in balance_changes.items():
        new_balance = balances[account_id] + change
        account = accounts[account_id]
        if new_balance < 0 and not (
            account.account_type is AccountType.CASH and allow_negative_cash_balance
        ):
            raise ValueError(
                f"Transaction {transaction.id} would make account {account_id} negative: "
                f"{new_balance}"
            )

    for account_id, change in balance_changes.items():
        balances[account_id] += change

    return TransactionLedgerEntry(
        transaction_id=transaction.id,
        year=transaction.year,
        transaction_type=transaction.transaction_type,
        amount=amount,
        source_account_id=transaction.source_account_id,
        destination_account_id=transaction.destination_account_id,
        charitable_method=charitable_method,
        spendable_income=spendable_income,
        cash_withdrawal=cash_withdrawal,
        spending=spending,
        contribution=contribution,
        federal_tax_payment=federal_tax_payment,
        federal_tax_refund=federal_tax_refund,
        federal_income_tax_withholding=federal_income_tax_withholding,
        taxable_ordinary_income=taxable_ordinary_income,
        missouri_tax_payment=missouri_tax_payment,
        missouri_tax_refund=missouri_tax_refund,
        state_income_tax_withholding=state_income_tax_withholding,
        medicare_payment=medicare_payment,
        taxable_amount=(
            transaction.taxable_amount
            if transaction.taxable_amount is not None
            else amount
            if transaction.transaction_type is TransactionType.ROTH_CONVERSION
            else None
        ),
        roth_conversion_method=transaction.roth_conversion_method,
        rmd_obligation_group_id=transaction.rmd_obligation_group_id,
        rmd_obligation_group_type=transaction.rmd_obligation_group_type,
    )


def _account(
    account_id: str | None, accounts: dict[str, AccountInput], role: str
) -> AccountInput | None:
    if account_id is None:
        return None
    try:
        return accounts[account_id]
    except KeyError as error:
        raise ValueError(f"Unknown {role} account: {account_id}") from error


def _require_present(account: AccountInput | None, message: str) -> AccountInput:
    if account is None:
        raise ValueError(message)
    return account


def _require_absent(account: AccountInput | None, message: str) -> None:
    if account is not None:
        raise ValueError(message)


def _require_type(
    account: AccountInput | None, allowed_types: set[AccountType], message: str
) -> AccountInput:
    if account is None or account.account_type not in allowed_types:
        raise ValueError(message)
    return account


def _require_distinct(source: AccountInput | None, destination: AccountInput | None) -> None:
    if source is not None and destination is not None and source.id == destination.id:
        raise ValueError("Source and destination accounts must differ")
