from typing import Protocol

from retirement_core.rules.models import RuleDataset


class RuleDatasetProvider(Protocol):
    def get_dataset(self, dataset_type: str, jurisdiction: str, year: int) -> RuleDataset: ...
