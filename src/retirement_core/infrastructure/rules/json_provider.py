import re
from datetime import date
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

    def get_applicable_dataset(
        self, dataset_type: str, jurisdiction: str, year: int
    ) -> RuleDataset:
        directory = self._base_path / dataset_type / jurisdiction
        year_end = date(year, 12, 31)
        candidates: list[RuleDataset] = []
        if directory.exists():
            for path in directory.glob("*.json"):
                dataset = RuleDataset.model_validate_json(path.read_text(encoding="utf-8"))
                if dataset.effective_from is None:
                    continue
                if dataset.effective_from <= year_end and (
                    dataset.effective_to is None or year_end <= dataset.effective_to
                ):
                    candidates.append(dataset)
        if not candidates:
            raise FileNotFoundError(
                f"No applicable rule dataset for type={dataset_type}, "
                f"jurisdiction={jurisdiction}, year={year}"
            )
        return max(
            candidates,
            key=lambda item: (
                item.effective_from,
                item.tax_year or item.premium_year or -1,
                _version_key(item.version),
            ),
        )


def _version_key(version: str) -> tuple[tuple[int, int | str], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in re.split(r"([0-9]+)", version)
        if part
    )
