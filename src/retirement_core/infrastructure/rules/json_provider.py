from pathlib import Path

from retirement_core.rules.models import RuleDataset


class JsonRuleDatasetProvider:
    def __init__(self, base_path: Path) -> None:
        self._base_path = base_path

    def get_dataset(self, dataset_type: str, jurisdiction: str, year: int) -> RuleDataset:
        path = self._base_path / dataset_type / jurisdiction / f"{year}.json"
        if not path.exists():
            raise FileNotFoundError(
                f"No rule dataset for type={dataset_type}, jurisdiction={jurisdiction}, year={year}"
            )
        return RuleDataset.model_validate_json(path.read_text(encoding="utf-8"))
