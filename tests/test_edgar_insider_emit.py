from datetime import date

from shortlist.edgar.insider import InsiderTxn, emissions_from_txns, qualifies

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


def test_mutual_fund_suffix_never_emits():
    """A 5th-letter `X` is Nasdaq's open-end-fund marker, and three of them reached the
    live picks ledger through this originator (FTECX, VFLEX, BBASX 2026-07-08..07-27).
    BBASX scored composite 100.0 UNGATED and entered the digest as a top-ranked "stock" —
    a mutual fund cannot be an insider-buy issuer, so the emission is a resolver artifact.
    Evidence: docs/audits/2026-08-07-funnel-gate-mismatch.md §3."""
    ems = emissions_from_txns([_t("A", "BBASX", 200_000)], _idx_opportunistic("A"),
                              date(2025, 6, 3), CFG)
    assert ems == []


def test_four_char_ticker_ending_in_a_suffix_letter_still_emits():
    """The rule is 5-letter-only. WOOF/ONEX must not be collateral damage."""
    ems = emissions_from_txns([_t("A", "WOOF", 200_000)], _idx_opportunistic("A"),
                              date(2025, 6, 3), CFG)
    assert [e.ticker for e in ems] == ["WOOF"]


def test_ordinary_five_letter_ticker_still_emits():
    """GOOGL is 5 letters and must survive — only the suffix LETTERS are junk markers."""
    ems = emissions_from_txns([_t("A", "GOOGL", 200_000)], _idx_opportunistic("A"),
                              date(2025, 6, 3), CFG)
    assert [e.ticker for e in ems] == ["GOOGL"]


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


# --- placeholder tickers must never form a phantom bucket (final review C-1) ----------
# Regression for a bug observed in PRODUCTION and previously guarded by
# edgar_index._is_real_ticker, whose Form 4 call sites were deleted with
# cluster_buys_from_records. Verified against real SEC data: of 57,797 Form 4 filings in
# 2025Q1, 459 (0.79%) carry a placeholder issuerTradingSymbol -- NONE x305, N/A x91,
# "-" x42, NA x15, and a bare CIK x6. All are TRUTHY, so a `if not t.ticker` filter passes
# them, and emissions_from_txns buckets by ticker -- merging unrelated companies into one
# emission with summed dollars and pooled owner counts, at near-max strength.

def _pt(owner, ticker, issuer_cik, value=200_000):
    from datetime import date as _d
    return InsiderTxn(owner_cik=owner, ticker=ticker, date=_d(2025, 6, 2), code="P",
                      shares=value / 10.0, price=10.0, plan_10b5_1=False,
                      roles=frozenset({"officer"}), title=None, joint_filing=False,
                      issuer_cik=issuer_cik)


def _opp(*owners):
    return {o.strip().zfill(10): {(2024, 1), (2023, 5), (2022, 9)} for o in owners}


def test_placeholder_tickers_do_not_form_a_phantom_bucket():
    """NONE / N/A / NA / "-" / a bare CIK are all truthy and would otherwise merge."""
    from datetime import date as _d
    txns = [_pt("A", "NONE", "0000000001"), _pt("B", "N/A", "0000000002"),
            _pt("C", "NA", "0000000003"), _pt("D", "-", "0000000004"),
            _pt("E", "1314152", "0000000005")]
    ems = emissions_from_txns(txns, _opp("A", "B", "C", "D", "E"), _d(2025, 6, 3), CFG)
    assert ems == [], f"placeholder tickers leaked: {[e.ticker for e in ems]}"


def test_a_real_ticker_alongside_placeholders_still_emits():
    """The guard must not suppress legitimate names sharing the batch."""
    from datetime import date as _d
    txns = [_pt("A", "NONE", "0000000001"), _pt("B", "OKLO", "0001849056")]
    ems = emissions_from_txns(txns, _opp("A", "B"), _d(2025, 6, 3), CFG)
    assert [e.ticker for e in ems] == ["OKLO"]


def test_one_ticker_with_conflicting_issuer_ciks_does_not_emit():
    """Incoherent: same symbol, two different issuers. Abstain rather than pick one --
    Emission.cik is persisted to the firehose as the permanent measurement record."""
    from datetime import date as _d
    txns = [_pt("A", "ZZZ", "0000000001"), _pt("B", "ZZZ", "0000000002")]
    ems = emissions_from_txns(txns, _opp("A", "B"), _d(2025, 6, 3), CFG)
    assert ems == []
