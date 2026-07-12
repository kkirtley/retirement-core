import json
from pathlib import Path

from retirement_core.domain.models import ProjectionRequest
from retirement_core.engine.projection import run_projection


def test_baseline_projection_reconciles() -> None:
    payload = json.loads(Path("examples/baseline_plan.json").read_text(encoding="utf-8"))
    request = ProjectionRequest.model_validate(payload)
    result = run_projection(request)
    assert result.engine_version == "0.1.0"
    assert len(result.annual_accounts) == 3
    assert result.annual_accounts[-1].ending_balance > result.annual_accounts[0].ending_balance
