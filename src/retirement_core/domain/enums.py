from enum import StrEnum


class FilingStatus(StrEnum):
    MARRIED_FILING_JOINTLY = "married_filing_jointly"
    SINGLE = "single"
    HEAD_OF_HOUSEHOLD = "head_of_household"
    MARRIED_FILING_SEPARATELY = "married_filing_separately"


class IncomeType(StrEnum):
    PENSION = "pension"
    UNSPECIFIED = "unspecified"


class SocialSecurityBenefitSubtype(StrEnum):
    RETIREMENT = "retirement"
    DISABILITY = "disability"
    SURVIVOR = "survivor"


class QcdTargetMode(StrEnum):
    NONE = "none"
    FIXED_FLOOR = "fixed_floor"
    HOUSEHOLD_RMD = "household_rmd"
    MAX_OF_FLOOR_AND_HOUSEHOLD_RMD = "max_of_floor_and_household_rmd"


class QcdAllocationMethod(StrEnum):
    PROPORTIONAL_TO_OWNER_RMD = "proportional_to_owner_rmd"
    OWNER_PRIORITY = "owner_priority"
    ACCOUNT_PRIORITY = "account_priority"


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
    SOCIAL_SECURITY_INCOME = "social_security_income"


class CharitableGivingMethod(StrEnum):
    CASH = "cash"
    QCD = "qcd"


class DatasetStatus(StrEnum):
    ENACTED = "enacted"
    PROJECTED = "projected"
    HISTORICAL = "historical"
