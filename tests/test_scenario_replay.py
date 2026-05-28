from pathlib import Path

import pytest

from tests.helpers.scenario_replay import (
    assert_replay_matches_expected,
    load_scenario,
    replay_scenario,
    scenario_paths,
)

SCENARIOS_DIR = Path(__file__).parent / "fixtures" / "scenarios"


@pytest.mark.parametrize("scenario_path", scenario_paths(SCENARIOS_DIR), ids=lambda path: path.stem)
def test_proactive_policy_replays_scenario(scenario_path: Path) -> None:
    with replay_scenario(scenario_path) as result:
        assert_replay_matches_expected(result)


def test_scenario_loader_validates_required_fields(tmp_path: Path) -> None:
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text('{"name": "missing expected", "now": "2026-05-28T10:00:00+00:00"}', encoding="utf-8")

    with pytest.raises(ValueError, match="expected must be dict"):
        load_scenario(invalid_path)
