from __future__ import annotations

import inspect
from typing import Optional

from ...env import redact_secrets
from ._common import _load_ticker_name_index, _read_versioned_cache, _write_versioned_cache  # noqa: F401
from .base import Source, _fetch_sections, _KeyedHttpSource, _retry_after_backoff  # noqa: F401
from .edgar import EdgarSource, _edgar_semaphore, build_events_section, classify_event_form  # noqa: F401
from .finnhub import FinnhubSource, _earnings, _news_flow, _normalize_finnhub  # noqa: F401
from .finra import (  # noqa: F401
    FinraSource,
    _finra_index,
    _finra_latest_partition,
    _finra_norm_symbol,
    _finra_row_to_si,
)
from .fmp import FMPSource, _match, _normalize_fmp, _year  # noqa: F401
from .govcontracts import GovContractsSource  # noqa: F401
from .lobbying import LobbyingSource  # noqa: F401
from .mock import MockSource  # noqa: F401
from .wsb import WsbSource  # noqa: F401
from .yahoo import YahooSource  # noqa: F401
from .yahoo_prices import (  # noqa: F401
    _MAX_RET_WINDOW,
    _MOM_12_1_BACK,
    _MOM_SKIP,
    _PCT_52W_HIGH_MIN_HISTORY,
    _PCT_52W_HIGH_WINDOW,
    _VOL_FLOOR,
    _VOL_SCALE_VOL_WINDOW,
    _YH_SIX_MONTHS,
    _YH_VOL_WINDOW,
    _chart_ts_and_series,
    _closes_from_chart,
    _dates_from_chart,
    _monthly_closes_from_chart,
    _normalize_yahoo,
    _yh_annualized_vol,
    _yh_max_drawdown,
    _yh_ret_over,
    _yh_sma,
    max_daily_return,
    mom_6m,
    mom_12_1,
    pct_to_52w_high,
    ret_between,
    snapshot_from_closes,
    snapshot_from_closes_dated,
    vol_scaled_momentum,
)

_REGISTRY = {
    "yahoo": YahooSource,
    "fmp": FMPSource, "finnhub": FinnhubSource, "edgar": EdgarSource,
    "finra": FinraSource, "mock": MockSource,
    "wsb": WsbSource, "gov_contracts": GovContractsSource,
    "lobbying": LobbyingSource,
}


def build_sources(names: list[str], config: Optional[dict] = None) -> list[Source]:
    out, skipped = [], []
    for n in names:
        if n not in _REGISTRY:
            raise ValueError(f"unknown source '{n}'. Known: {list(_REGISTRY)}")
        cls = _REGISTRY[n]
        try:
            # Only sources whose __init__ accepts `config` receive it; others stay zero-arg.
            if "config" in inspect.signature(cls.__init__).parameters:
                out.append(cls(config=config))
            else:
                out.append(cls())
        except Exception as e:
            skipped.append(f"{n} ({redact_secrets(e)})")
    if skipped:
        print(f"  ! skipped sources: {', '.join(skipped)}")
    return out
