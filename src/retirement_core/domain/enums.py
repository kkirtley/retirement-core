from enum import StrEnum


class FilingStatus(StrEnum):
    MARRIED_FILING_JOINTLY = "married_filing_jointly"
    SINGLE = "single"
    HEAD_OF_HOUSEHOLD = "head_of_household"
    MARRIED_FILING_SEPARATELY = "married_filing_separately"


class IncomeType(StrEnum):
    PENSION = "pension"
    UNSPECIFIED = "unspecified"


class AccountType(StrEnum):
    TRADITIONAL_IRA = "traditional_ira"
    ROTH_IRA = "roth_ira"
    TRADITIONAL_401K = "traditional_401k"
    ROTH_401K = "roth_401k"
    HSA = "hsa"
    TAXABLE = "taxable"
    CASH = "cash"


class TransactionType(StrEnum):
    INCOME = "income"
    SPENDING = "spending"
    CONTRIBUTION = "contribution"
    WITHDRAWAL = "withdrawal"
    TRANSFER = "transfer"
    ROTH_CONVERSION = "roth_conversion"
    CHARITABLE_GIVING = "charitable_giving"
    FEDERAL_TAX_PAYMENT = "federal_tax_payment"


class CharitableGivingMethod(StrEnum):
    CASH = "cash"
    QCD = "qcd"


class DatasetStatus(StrEnum):
    ENACTED = "enacted"
    PROJECTED = "projected"
    HISTORICAL = "historical"
