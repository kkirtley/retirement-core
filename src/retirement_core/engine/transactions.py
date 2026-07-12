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
    charitable_method = transaction.charitable_method

    match transaction.transaction_type:
        case TransactionType.INCOME:
            _require_absent(source, "Income cannot have a source account")
            destination = _require_type(
                destination, {AccountType.CASH}, "Income destination must be cash"
            )
            credit(destination)
            activity[destination.id].contributions += amount
            spendable_income = amount
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
                raise ValueError("QCD transactions are reserved for a future implementation")
            _require_absent(destination, "Cash charitable giving cannot have a destination account")
            source = _require_type(
                source,
                {AccountType.CASH, AccountType.TAXABLE},
                "Cash charitable giving source must be cash or taxable",
            )
            debit(source)
            activity[source.id].withdrawals += amount
            spending = amount

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
