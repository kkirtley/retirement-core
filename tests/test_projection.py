import json
from pathlib import Path

from retirement_core.domain.models import ProjectionRequest
from retirement_core.engine.projection import run_projection


def test_baseline_projection_reconciles() -> None:
    payload = json.loads(Path("examples/baseline_plan.json").read_text(encoding="utf-8"))
    request = ProjectionRequest.model_validate(payload)
    result = run_projection(request)
    assert result.engine_version == "0.1.0"
    roth_results = [row for row in result.annual_accounts if row.account_id == "person_a_roth"]
    assert len(result.annual_accounts) == 6
    assert roth_results[-1].ending_balance > roth_results[0].ending_balance
    assert result.annual_household[-1].cash_surplus == 5000
