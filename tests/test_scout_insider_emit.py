from datetime import date

from shortlist.scout.insider import InsiderTxn, emissions_from_txns, qualifies

CFG = {"min_value": 100000, "roles": ["officer", "director"],
       "exclude_10b5_1": True,
       "tier_strength": {"opportunistic": 1.0, "unclassified": 0.6}}


def _t(owner, ticker, value, code="P", plan=False, roles=("officer",), title=None,
       joint_filing=False, issuer_cik=""):
    return InsiderTxn(owner_cik=owner, ticker=ticker, date=date(2025, 6, 2), code=code,
                      shares=value / 10.0, price=10.0, plan_10b5_1=plan,
                      roles=frozenset(roles), title=title, joint_filing=joint_filing,
                      issuer_cik=issuer_cik)


def _idx_opportunistic(owner):
    # classify_tier zero-pads owner_cik to 10 chars before the dict lookup (its own
    # docstring: this is the join key against build_trade_month_index, which always
    # emits zfilled keys) -- so the index fixture must match that canonical form, not
    # the raw single-letter test id used elsewhere as the transaction's owner_cik.
    return {owner.zfill(10): {(2024, 1), (2023, 5), (2022, 9)}}


def test_below_the_dollar_floor_does_not_emit():
    ems = emissions_from_txns([_t("A", "ZZZ", 50_000)], _idx_opportunistic("A"),
                              date(2025, 6, 3), CFG)
    assert ems == []


def test_floor_is_per_transaction_never_an_aggregate():
    """Five sub-floor trades must NOT sum past the bar -- that reintroduces the noise
    the floor exists to remove (docs/FORM4_INSIDER.md §7)."""
    txns = [_t(f"A{i}", "ZZZ", 30_000) for i in range(5)]
    idx = {}
    for i in range(5):
        idx.update(_idx_opportunistic(f"A{i}"))
    assert emissions_from_txns(txns, idx, date(2025, 6, 3), CFG) == []


def test_a_qualifying_buy_emits_once_per_issuer():
    txns = [_t("A", "ZZZ", 200_000), _t("B", "ZZZ", 300_000)]
    idx = {**_idx_opportunistic("A"), **_idx_opportunistic("B")}
    ems = emissions_from_txns(txns, idx, date(2025, 6, 3), CFG)
    assert len(ems) == 1 and ems[0].ticker == "ZZZ"


def test_cluster_scores_above_a_lone_buyer_of_the_same_size():
    lone = emissions_from_txns([_t("A", "ZZZ", 200_000)],
                               _idx_opportunistic("A"), date(2025, 6, 3), CFG)
    idx = {**_idx_opportunistic("A"), **_idx_opportunistic("B")}
    clust = emissions_from_txns([_t("A", "ZZZ", 100_000), _t("B", "ZZZ", 100_000)],
                                idx, date(2025, 6, 3), CFG)
    assert clust[0].strength > lone[0].strength


def test_10b5_1_planned_buys_are_excluded():
    ems = emissions_from_txns([_t("A", "ZZZ", 200_000, plan=True)],
                              _idx_opportunistic("A"), date(2025, 6, 3), CFG)
    assert ems == []


def test_non_purchase_codes_are_excluded():
    ems = emissions_from_txns([_t("A", "ZZZ", 200_000, code="S")],
                              _idx_opportunistic("A"), date(2025, 6, 3), CFG)
    assert ems == []


def test_routine_insiders_are_dropped():
    routine = {"A".zfill(10): {(2024, 6), (2023, 6), (2022, 6)}}  # see _idx_opportunistic
    ems = emissions_from_txns([_t("A", "ZZZ", 200_000)], routine, date(2025, 6, 3), CFG)
    assert ems == []


def test_unclassified_emits_at_reduced_strength_and_records_its_tier():
    opp = emissions_from_txns([_t("A", "ZZZ", 200_000)],
                              _idx_opportunistic("A"), date(2025, 6, 3), CFG)
    unc = emissions_from_txns([_t("B", "ZZZ", 200_000)], {}, date(2025, 6, 3), CFG)
    assert unc and unc[0].strength < opp[0].strength
    assert unc[0].meta["tier"] == "unclassified"
    assert opp[0].meta["tier"] == "opportunistic"


def test_joint_filings_are_excluded_from_qualifying():
    """A joint filing has no per-owner attribution -- owner_cik, and every tier derived
    from it, would be a guess. qualifies() must reject it regardless of size/tier."""
    txn = _t("A", "ZZZ", 200_000, joint_filing=True)
    assert qualifies(txn, "opportunistic", CFG) is False
    ems = emissions_from_txns([txn], _idx_opportunistic("A"), date(2025, 6, 3), CFG)
    assert ems == []


def test_emission_carries_issuer_cik():
    txns = [_t("A", "ZZZ", 200_000, issuer_cik="0001849056")]
    ems = emissions_from_txns(txns, _idx_opportunistic("A"), date(2025, 6, 3), CFG)
    assert ems[0].cik == "0001849056"
