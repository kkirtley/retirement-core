from enum import StrEnum


class FilingStatus(StrEnum):
    MARRIED_FILING_JOINTLY = "married_filing_jointly"
    SINGLE = "single"
    HEAD_OF_HOUSEHOLD = "head_of_household"
    MARRIED_FILING_SEPARATELY = "married_filing_separately"


class IncomeType(StrEnum):
    PENSION = "pension"
    TAXABLE_INTEREST = "taxable_interest"
    TAX_EXEMPT_INTEREST = "tax_exempt_interest"
    UNSPECIFIED = "unspecified"


class PensionType(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"


class RothConversionMethod(StrEnum):
    DIRECT = "direct"
    TRUSTEE_TO_TRUSTEE = "trustee_to_trustee"
    SIXTY_DAY_ROLLOVER = "sixty_day_rollover"


class ResidencyStatus(StrEnum):
    FULL_YEAR_RESIDENT = "full_year_resident"


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


class TaxableRmdAllocationMethod(StrEnum):
    PROPORTIONAL_TO_ACCOUNT_RMD = "proportional_to_account_rmd"
    ACCOUNT_PRIORITY = "account_priority"
    EXPLICIT_ACCOUNT_AMOUNTS = "explicit_account_amounts"
    STABLE_ACCOUNT_ID = "stable_account_id"


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
    RMD_DISTRIBUTION = "rmd_distribution"
    MISSOURI_TAX_PAYMENT = "missouri_tax_payment"


class CharitableGivingMethod(StrEnum):
    CASH = "cash"
    QCD = "qcd"


class DatasetStatus(StrEnum):
    ENACTED = "enacted"
    PROJECTED = "projected"
    HISTORICAL = "historical"


class MedicareBasePremiumMode(StrEnum):
    MODELED_SEPARATELY = "modeled_separately"
    INCLUDED_IN_SPENDING = "included_in_spending"


class FederalAgiComponentType(StrEnum):
    TAXABLE_PENSION = "taxable_pension"
    TAXABLE_RMD_DISTRIBUTION = "taxable_rmd_distribution"
    TAXABLE_NON_RMD_IRA_DISTRIBUTION = "taxable_non_rmd_ira_distribution"
    FEDERALLY_TAXABLE_ROTH_CONVERSION = "federally_taxable_roth_conversion"
    FEDERALLY_TAXABLE_SOCIAL_SECURITY = "federally_taxable_social_security"
    TAXABLE_INTEREST = "taxable_interest"
    TAX_EXEMPT_INTEREST = "tax_exempt_interest"
    OTHER_SUPPORTED_AGI = "other_supported_agi"
    ADJUSTMENT_TO_INCOME = "adjustment_to_income"
    QCD = "qcd"
    PRETAX_ROLLOVER = "pretax_rollover"
