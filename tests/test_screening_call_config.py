import yaml
from pathlib import Path


def test_screening_call_config_present_and_on():
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    sc = cfg["research"]["screening_call"]
    assert sc["enabled"] is True
    assert sc["labels"]["STRONG_BUY"] == "Strong Buy"
    assert sc["gate_clamp"]["_default"] == "HOLD"
    assert sc["gate_clamp"]["negative_fcf"] == "AVOID"
    assert sc["conviction_cap"]["low_below"] == 0.45
    assert sc["conviction_cap"]["medium_below"] == 0.70
    assert sc["high_conviction"]["contra_flags"] == ["value_trap"]
    assert sc["disclaimer"]
