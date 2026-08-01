import pandas as pd
import pytest

from core.market_data import fetch_stock_profile, fetch_usd_thb_rate


class FakeTicker:
    def __init__(self, info=None, history_df=None, raise_on_history=False, dividends=None):
        self.info = info
        self._history_df = history_df
        self._raise_on_history = raise_on_history
        self.history_call_kwargs = None
        self.dividends = dividends if dividends is not None else pd.Series([], dtype=float)

    def history(self, start=None, end=None):
        self.history_call_kwargs = {"start": start, "end": end}
        if self._raise_on_history:
            raise RuntimeError("simulated network failure")
        return self._history_df


class FakeYfModule:
    def __init__(self, tickers: dict):
        self._tickers = tickers

    def Ticker(self, symbol):
        return self._tickers[symbol]


def _history(closes):
    return pd.DataFrame({"Close": closes})


def _dividends(payouts: dict):
    """payouts: {days_ago: amount} -- builds a pd.Series with a datetime index,
    matching yfinance's real Ticker.dividends shape."""
    today = pd.Timestamp.today().normalize()
    index = [today - pd.Timedelta(days=d) for d in payouts]
    return pd.Series(list(payouts.values()), index=pd.DatetimeIndex(index))


class TestFetchStockProfile:
    def test_happy_path_returns_one_row_per_symbol(self):
        yf_module = FakeYfModule({
            "KO": FakeTicker(
                info={"longName": "The Coca-Cola Company", "sector": "Consumer Staples",
                      "industry": "Beverages", "beta": 0.349, "quoteType": "EQUITY"},
                history_df=_history([80.0, 81.0, 83.635]),
            ),
            "SHV": FakeTicker(
                info={"longName": "iShares 0-1 Year Treasury Bond ETF", "sector": None,
                      "industry": None, "beta": None, "quoteType": "ETF"},
                history_df=_history([110.25, 110.27, 110.305]),
            ),
        })

        result = fetch_stock_profile(["KO", "SHV"], yf_module=yf_module)

        assert list(result["Symbol"]) == ["KO", "SHV"]
        ko = result[result["Symbol"] == "KO"].iloc[0]
        assert ko["Description"] == "The Coca-Cola Company"
        assert ko["Sector"] == "Consumer Staples"
        assert ko["Industry"] == "Beverages"
        assert ko["Quote Type"] == "EQUITY"
        assert ko["Beta"] == pytest.approx(0.349)
        assert ko["Latest Price"] == pytest.approx(83.635)
        assert ko["History90D"] == [80.0, 81.0, 83.635]
        # ETFs typically have no sector/industry classification in yfinance -- real, not an error.
        shv = result[result["Symbol"] == "SHV"].iloc[0]
        assert pd.isna(shv["Sector"])
        assert pd.isna(shv["Industry"])
        assert shv["Quote Type"] == "ETF"

    def test_etf_sector_and_industry_fall_back_to_fund_family_and_category(self):
        # Real finding: yfinance leaves sector/industry blank for ETFs, but fundFamily/category
        # (Morningstar-style fund classification) are populated -- confirmed for SHV/SGOV/SJNK/etc.
        yf_module = FakeYfModule({
            "SHV": FakeTicker(
                info={"longName": "iShares 0-1 Year Treasury Bond ETF", "sector": None,
                      "industry": None, "fundFamily": "iShares", "category": "Ultrashort Bond", "beta": None},
                history_df=_history([110.25, 110.27, 110.305]),
            ),
        })
        result = fetch_stock_profile(["SHV"], yf_module=yf_module)
        shv = result.iloc[0]
        assert shv["Sector"] == "iShares"
        assert shv["Industry"] == "Ultrashort Bond"

    def test_equity_sector_and_industry_are_not_overridden_by_fallback(self):
        # An equity's real sector/industry must win even if fundFamily/category happen to be set --
        # the fallback should only fire when the equity-specific field is blank.
        yf_module = FakeYfModule({
            "KO": FakeTicker(
                info={"longName": "The Coca-Cola Company", "sector": "Consumer Staples",
                      "industry": "Beverages", "fundFamily": "Should Not Appear",
                      "category": "Should Not Appear", "beta": 0.349},
                history_df=_history([80.0, 81.0, 83.635]),
            ),
        })
        result = fetch_stock_profile(["KO"], yf_module=yf_module)
        ko = result.iloc[0]
        assert ko["Sector"] == "Consumer Staples"
        assert ko["Industry"] == "Beverages"

    def test_etf_beta_falls_back_to_beta3year(self):
        # Real finding: yfinance leaves `beta` blank for most ETFs, but `beta3Year`
        # (Yahoo's 3-year-monthly figure, the convention funds are typically reported
        # under) is populated instead -- confirmed for SHV (matches finviz.com's own
        # ETF Beta value of 0.01 for SHV).
        yf_module = FakeYfModule({
            "SHV": FakeTicker(
                info={"longName": "iShares 0-1 Year Treasury Bond ETF", "sector": None,
                      "industry": None, "beta": None, "beta3Year": 0.01},
                history_df=_history([110.25, 110.27, 110.305]),
            ),
        })
        result = fetch_stock_profile(["SHV"], yf_module=yf_module)
        assert result.iloc[0]["Beta"] == pytest.approx(0.01)

    def test_equity_beta_is_not_overridden_by_beta3year_fallback(self):
        # An equity's real beta must win even if beta3Year happens to be set -- the
        # fallback should only fire when the equity-specific field is blank.
        yf_module = FakeYfModule({
            "KO": FakeTicker(
                info={"longName": "The Coca-Cola Company", "beta": 0.349, "beta3Year": 99.0},
                history_df=_history([80.0, 81.0, 83.635]),
            ),
        })
        result = fetch_stock_profile(["KO"], yf_module=yf_module)
        assert result.iloc[0]["Beta"] == pytest.approx(0.349)

    def test_dividend_per_year_yield_and_frequency_from_trailing_365_day_payouts(self):
        # Real finding: yfinance's dividendRate/trailingAnnualDividendRate info-dict fields
        # are blank/$0.00 for ETFs (confirmed for SHV, SCHD) despite real payouts existing in
        # Ticker.dividends. Computed here from 12 monthly-ish payouts within the last year,
        # matching the real SHV shape confirmed in chat ($4.168/yr, 12 payouts, Monthly).
        yf_module = FakeYfModule({
            "SHV": FakeTicker(
                info={"longName": "iShares 0-1 Year Treasury Bond ETF"},
                history_df=_history([110.25, 110.27, 110.30]),
                dividends=_dividends({d: 0.347 for d in range(15, 366, 30)}),  # 12 payouts
            ),
        })
        result = fetch_stock_profile(["SHV"], yf_module=yf_module)
        shv = result.iloc[0]
        assert shv["Dividend Per Year"] == pytest.approx(0.347 * 12)
        assert shv["Dividend Yield %"] == pytest.approx(0.347 * 12 / 110.30 * 100)
        assert shv["Dividend Frequency"] == "Monthly"

    def test_dividend_payouts_older_than_365_days_are_excluded(self):
        yf_module = FakeYfModule({
            "X": FakeTicker(
                info={"longName": "X Corp"},
                history_df=_history([100.0]),
                dividends=_dividends({30: 1.0, 90: 1.0, 400: 1.0}),  # last one is outside the window
            ),
        })
        result = fetch_stock_profile(["X"], yf_module=yf_module)
        x = result.iloc[0]
        assert x["Dividend Per Year"] == pytest.approx(2.0)
        assert x["Dividend Frequency"] == "Semi-Annual"

    def test_non_dividend_payer_gets_zero_not_nan(self):
        # Real finding: ARM (a real non-dividend-paying equity) returns a clean empty
        # Series from Ticker.dividends, not an exception -- "doesn't pay dividends" is
        # valid data, distinct from a fetch failure.
        yf_module = FakeYfModule({
            "ARM": FakeTicker(info={"longName": "Arm Holdings"}, history_df=_history([150.0])),
        })
        result = fetch_stock_profile(["ARM"], yf_module=yf_module)
        arm = result.iloc[0]
        assert arm["Dividend Per Year"] == 0.0
        assert arm["Dividend Yield %"] == 0.0
        assert arm["Dividend Frequency"] == "None"

    def test_falls_back_to_shortname_when_longname_missing(self):
        yf_module = FakeYfModule({
            "X": FakeTicker(info={"shortName": "X Corp"}, history_df=_history([1.0, 2.0])),
        })
        result = fetch_stock_profile(["X"], yf_module=yf_module)
        assert result.iloc[0]["Description"] == "X Corp"

    def test_missing_beta_becomes_nan_not_an_error(self):
        # Real finding from Step 0: SHV (a short-duration bond ETF) genuinely has beta=None.
        yf_module = FakeYfModule({
            "SHV": FakeTicker(
                info={"longName": "iShares 0-1 Year Treasury Bond ETF", "sector": None,
                      "industry": None, "beta": None},
                history_df=_history([110.25, 110.27, 110.305]),
            ),
        })
        result = fetch_stock_profile(["SHV"], yf_module=yf_module)
        assert pd.isna(result.iloc[0]["Beta"])

    def test_unresolvable_symbol_gets_nan_row_and_does_not_affect_other_symbols(self):
        yf_module = FakeYfModule({
            "GOOD": FakeTicker(info={"longName": "Good Co", "sector": "Technology", "industry": "Software", "beta": 1.0},
                               history_df=_history([10.0, 11.0])),
            "BAD": FakeTicker(info=None, history_df=None),  # simulates an unresolvable/delisted symbol
        })
        result = fetch_stock_profile(["GOOD", "BAD"], yf_module=yf_module)

        assert len(result) == 2
        bad = result[result["Symbol"] == "BAD"].iloc[0]
        assert pd.isna(bad["Beta"])
        assert pd.isna(bad["Latest Price"])
        assert bad["History90D"] == []
        assert pd.isna(bad["Dividend Per Year"])
        assert pd.isna(bad["Dividend Yield %"])
        assert pd.isna(bad["Dividend Frequency"])
        good = result[result["Symbol"] == "GOOD"].iloc[0]
        assert good["Latest Price"] == pytest.approx(11.0)

    def test_symbol_raising_an_exception_gets_nan_row_not_a_crash(self):
        yf_module = FakeYfModule({"BOOM": FakeTicker(raise_on_history=True)})
        result = fetch_stock_profile(["BOOM"], yf_module=yf_module)
        assert len(result) == 1
        assert pd.isna(result.iloc[0]["Latest Price"])

    def test_empty_history_dataframe_gets_nan_row_not_a_crash(self):
        yf_module = FakeYfModule({"EMPTY": FakeTicker(info={"longName": "Empty Co"}, history_df=_history([]))})
        result = fetch_stock_profile(["EMPTY"], yf_module=yf_module)
        assert pd.isna(result.iloc[0]["Latest Price"])

    def test_requests_a_90_calendar_day_window_not_yfinances_period_shorthand(self):
        # Regression test: yfinance's period="90d" shorthand actually returns 90 *trading*
        # days (~131 calendar days) -- confirmed by direct comparison against real data.
        # That silently diverged from the user's reference formula
        # (GOOGLEFINANCE(symbol, "Price", TODAY()-90, TODAY()), calendar-day arithmetic),
        # so fetch_stock_profile must call history() with explicit start/end dates instead.
        ticker = FakeTicker(info={"longName": "Test Co"}, history_df=_history([1.0, 2.0]))
        yf_module = FakeYfModule({"TEST": ticker})

        fetch_stock_profile(["TEST"], yf_module=yf_module)

        kwargs = ticker.history_call_kwargs
        assert kwargs["start"] is not None and kwargs["end"] is not None
        span_days = (kwargs["end"] - kwargs["start"]).days
        assert span_days == 91  # 90 days back + 1 (end is exclusive, so today's session is included)

    def test_empty_symbol_list_returns_empty_frame_without_error(self):
        result = fetch_stock_profile([], yf_module=FakeYfModule({}))
        assert result.empty
        assert list(result.columns) == ["Symbol", "Description", "Sector", "Industry", "Quote Type", "Beta",
                                         "History90D", "Latest Price", "Dividend Per Year", "Dividend Yield %",
                                         "Dividend Frequency"]
        assert result["Beta"].dtype == "float64"
        assert result["Symbol"].dtype == "object"


class TestFetchUsdThbRate:
    def test_happy_path_returns_last_close(self):
        yf_module = FakeYfModule({"THB=X": FakeTicker(history_df=_history([33.10, 33.15, 33.22]))})
        assert fetch_usd_thb_rate(yf_module=yf_module) == pytest.approx(33.22)

    def test_network_failure_returns_none_not_a_crash(self):
        yf_module = FakeYfModule({"THB=X": FakeTicker(raise_on_history=True)})
        assert fetch_usd_thb_rate(yf_module=yf_module) is None

    def test_empty_history_returns_none(self):
        yf_module = FakeYfModule({"THB=X": FakeTicker(history_df=_history([]))})
        assert fetch_usd_thb_rate(yf_module=yf_module) is None
