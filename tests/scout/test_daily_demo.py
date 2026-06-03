from shortlist.scout.daily import main


def test_demo_runs_offline_and_prints_report(capsys):
    rc = main(["--demo"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Scout shortlist" in out
    assert "Signals:" in out and "Funnel:" in out
    # demo uses MockSignal -> GEV/LMT/GOOGL discovered (tickers the mock provider knows)
    assert "GOOGL" in out
