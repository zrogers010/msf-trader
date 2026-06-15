from msf_trader.backtest.levels import Zone, cluster_into_zones, nearest_zone


def test_zone_n_sources_collapses_ma_periods():
    z = Zone(100.0, 100.0, "support", ("pivot", "ma20", "ma50"))
    # pivot + (all MAs count as a single "moving average" reason)
    assert z.n_sources == 2


def test_zone_score_caps_touches():
    z = Zone(100.0, 100.0, "support", ("pivot", "prior_day_low"))
    z.touches = 9
    assert z.score == 2 + 3  # two sources + touches capped at 3


def test_cluster_merges_within_width_and_mixes_kind():
    cands = [
        (100.0, "support", "pivot"),
        (101.0, "resistance", "round"),   # within 2.0 of 100 -> merges
        (110.0, "support", "prior_day_low"),
    ]
    zones = cluster_into_zones(cands, width=2.0)
    assert len(zones) == 2
    assert zones[0].kind == "both"  # support + resistance merged
    assert set(zones[0].sources) == {"pivot", "round"}
    assert zones[1].kind == "support"


def test_nearest_zone_respects_kind_and_proximity():
    zones = [
        Zone(100.0, 100.0, "support", ("pivot",)),
        Zone(120.0, 120.0, "resistance", ("pivot",)),
    ]
    assert nearest_zone(zones, 100.5, 2.0, "support") is zones[0]
    # a support-only zone cannot serve as resistance
    assert nearest_zone(zones, 100.5, 2.0, "resistance") is None
    # nothing within proximity
    assert nearest_zone(zones, 130.0, 2.0, "resistance") is None
