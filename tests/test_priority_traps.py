from python_validation.pipeline import _max_urgency, _priority_from_urgency


def test_calm_safety_stays_critical_over_low():
    assert _max_urgency("low", "critical") == "critical"


def test_angry_low_risk_maps_to_p3():
    assert _priority_from_urgency("low", vip=False) == "P3"


def test_vip_minor_issue_is_not_critical():
    assert _priority_from_urgency("low", vip=True) == "P2"
