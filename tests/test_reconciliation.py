import numpy as np
import pandas as pd
import pytest

from core.reconciliation import (
    _pair_1to1,
    load_xlsx_for_reconciliation,
    match_dividend_rows,
    match_dividends,
    match_interest_rows,
    match_trades,
    unmatched_xlsx_income,
    unmatched_xlsx_trades,
)


def make_sqlite_trades(rows):
    """Build a frame shaped like db.fetch_unreconciled_trades()'s output --
    only the columns match_trades actually reads. Each row is a dict;
    missing fields default to None so tests can stay terse."""
    columns = ["id", "Trade Date", "Symbol", "Quantity", "Price"]
    df = pd.DataFrame(rows)
    for col in columns:
        if col not in df.columns:
            df[col] = None
    df["Trade Date"] = pd.to_datetime(df["Trade Date"])
    # Real fetch_trades() reads via pd.read_sql_query, where a SQLite NULL in a
    # REAL column comes back as a proper float64 NaN -- match that here (a
    # single-row None otherwise stays object-dtype, which .round() can't handle).
    for col in ("Quantity", "Price"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[columns]


def make_xlsx_transactions(rows):
    """Build a frame shaped like the raw xlsx Transactions sheet (post
    load_xlsx_for_reconciliation parsing) -- only the columns match_trades
    actually reads."""
    columns = ["Trade Date", "Symbol", "Quantity", "Price"]
    df = pd.DataFrame(rows)
    for col in columns:
        if col not in df.columns:
            df[col] = None
    df["Trade Date"] = pd.to_datetime(df["Trade Date"])
    for col in ("Quantity", "Price"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[columns]


def sqlite_row(id, date, symbol, qty, price):
    return {"id": id, "Trade Date": date, "Symbol": symbol, "Quantity": qty, "Price": price}


def xlsx_row(date, symbol, qty, price):
    return {"Trade Date": date, "Symbol": symbol, "Quantity": qty, "Price": price}


def make_sqlite_dividends(rows):
    """Build a frame shaped like db.fetch_unreconciled_dividends()'s output --
    only the columns the dividend/interest matchers actually read."""
    columns = ["id", "Trade Date", "Symbol", "Entry Type", "Net Amt"]
    df = pd.DataFrame(rows)
    for col in columns:
        if col not in df.columns:
            df[col] = None
    df["Trade Date"] = pd.to_datetime(df["Trade Date"])
    df["Net Amt"] = pd.to_numeric(df["Net Amt"], errors="coerce")
    return df[columns]


def make_xlsx_income(rows):
    """Build a frame shaped like the raw xlsx Income sheet (post
    load_xlsx_for_reconciliation parsing)."""
    columns = ["Trade Date", "Entry Type", "Symbol", "Net Amt"]
    df = pd.DataFrame(rows)
    for col in columns:
        if col not in df.columns:
            df[col] = None
    df["Trade Date"] = pd.to_datetime(df["Trade Date"])
    df["Net Amt"] = pd.to_numeric(df["Net Amt"], errors="coerce")
    return df[columns]


def sqlite_dividend(id, date, symbol, entry_type, net_amt):
    return {"id": id, "Trade Date": date, "Symbol": symbol, "Entry Type": entry_type, "Net Amt": net_amt}


def xlsx_income_row(date, entry_type, symbol, net_amt):
    return {"Trade Date": date, "Entry Type": entry_type, "Symbol": symbol, "Net Amt": net_amt}


class TestPair1to1:
    def test_matches_on_shared_key(self):
        left = pd.DataFrame({"k": ["A"], "v": [1]})
        right = pd.DataFrame({"k": ["A"], "w": [2]})
        result = _pair_1to1(left, right, ["k"])
        assert result.iloc[0]["_merge"] == "both"
        assert result.iloc[0]["w"] == 2

    def test_no_match_is_left_only(self):
        left = pd.DataFrame({"k": ["A"], "v": [1]})
        right = pd.DataFrame({"k": ["B"], "w": [2]})
        result = _pair_1to1(left, right, ["k"])
        assert result.iloc[0]["_merge"] == "left_only"

    def test_duplicate_on_both_sides_pairs_one_to_one(self):
        left = pd.DataFrame({"k": ["A", "A"], "v": [1, 2]})
        right = pd.DataFrame({"k": ["A", "A"], "w": [10, 20]})
        result = _pair_1to1(left, right, ["k"])
        assert len(result) == 2
        assert (result["_merge"] == "both").all()

    def test_extra_duplicate_on_left_only_is_unmatched(self):
        # left has 2 rows with key "A", right only has 1 -- the excess left
        # row must NOT fan out and match the same right row twice.
        left = pd.DataFrame({"k": ["A", "A"], "v": [1, 2]})
        right = pd.DataFrame({"k": ["A"], "w": [10]})
        result = _pair_1to1(left, right, ["k"])
        assert len(result) == 2
        assert (result["_merge"] == "both").sum() == 1
        assert (result["_merge"] == "left_only").sum() == 1

    def test_nan_key_rows_still_rank_individually(self):
        # Two left rows both with a NaN key column -- dropna=False must give
        # them distinct ranks (0, 1) rather than collapsing into one group,
        # so they don't cross-match each other's xlsx counterpart.
        left = pd.DataFrame({"k": [np.nan, np.nan], "v": [1, 2]})
        right = pd.DataFrame({"k": [np.nan], "w": [10]})
        result = _pair_1to1(left, right, ["k"])
        assert len(result) == 2
        assert (result["_merge"] == "both").sum() == 1


class TestMatchTrades:
    def test_exact_match_happy_path(self):
        sqlite = make_sqlite_trades([sqlite_row(1, "2023-01-03", "MCHI", 0.297563, 48.83)])
        xlsx = make_xlsx_transactions([xlsx_row("2023-01-03", "MCHI", 0.297563, 48.83)])
        result = match_trades(sqlite, xlsx)
        assert len(result) == 1
        assert result.iloc[0]["matched"] is np.True_ or result.iloc[0]["matched"] is True
        assert pd.isna(result.iloc[0]["reason"])  # pandas normalizes a mapped None to NaN
        assert result.iloc[0]["xlsx_month"] == pd.Timestamp("2023-01-01")

    def test_no_match_at_all(self):
        sqlite = make_sqlite_trades([sqlite_row(1, "2023-01-03", "MCHI", 10, 5.0)])
        xlsx = make_xlsx_transactions([xlsx_row("2023-01-04", "VNAM", 1, 15.48)])
        result = match_trades(sqlite, xlsx)
        assert result.iloc[0]["matched"] == False  # noqa: E712
        assert result.iloc[0]["reason"] == "No matching xlsx trade found"

    def test_quantity_mismatch_is_not_matched_no_tolerance(self):
        sqlite = make_sqlite_trades([sqlite_row(1, "2023-01-03", "MCHI", 10.000001, 5.0)])
        xlsx = make_xlsx_transactions([xlsx_row("2023-01-03", "MCHI", 10.000002, 5.0)])
        # differ beyond the 6dp rounding tolerance -- must not match
        result = match_trades(sqlite, xlsx)
        assert result.iloc[0]["matched"] == False  # noqa: E712

    def test_price_mismatch_is_not_matched(self):
        sqlite = make_sqlite_trades([sqlite_row(1, "2023-01-03", "MCHI", 10, 5.00)])
        xlsx = make_xlsx_transactions([xlsx_row("2023-01-03", "MCHI", 10, 5.01)])
        result = match_trades(sqlite, xlsx)
        assert result.iloc[0]["matched"] == False  # noqa: E712

    def test_quantity_rounds_to_6dp_before_comparing(self):
        # xlsx carries more decimal places than the UI accepts -- rounding
        # to 6dp is a bounded precision alignment, not fuzzy matching.
        sqlite = make_sqlite_trades([sqlite_row(1, "2023-01-03", "MCHI", 0.297563, 48.83)])
        xlsx = make_xlsx_transactions([xlsx_row("2023-01-03", "MCHI", 0.2975634321, 48.83)])
        result = match_trades(sqlite, xlsx)
        assert result.iloc[0]["matched"] == True  # noqa: E712

    def test_price_rounds_to_4dp_before_comparing(self):
        sqlite = make_sqlite_trades([sqlite_row(1, "2023-01-03", "MCHI", 1, 48.8300)])
        xlsx = make_xlsx_transactions([xlsx_row("2023-01-03", "MCHI", 1, 48.82998)])
        result = match_trades(sqlite, xlsx)
        assert result.iloc[0]["matched"] == True  # noqa: E712

    def test_same_day_different_quantity_legs_both_match(self):
        # Whole-share + fractional-share fills on the same date/symbol at
        # the same price -- both are real, distinct rows and must both match.
        sqlite = make_sqlite_trades([
            sqlite_row(1, "2023-01-03", "MCHI", 1.0, 48.83),
            sqlite_row(2, "2023-01-03", "MCHI", 0.297563, 48.83),
        ])
        xlsx = make_xlsx_transactions([
            xlsx_row("2023-01-03", "MCHI", 1.0, 48.83),
            xlsx_row("2023-01-03", "MCHI", 0.297563, 48.83),
        ])
        result = match_trades(sqlite, xlsx)
        assert result["matched"].all()

    def test_duplicate_sqlite_rows_pair_one_to_one_with_duplicate_xlsx_rows(self):
        sqlite = make_sqlite_trades([
            sqlite_row(1, "2023-01-03", "MCHI", 1.0, 48.83),
            sqlite_row(2, "2023-01-03", "MCHI", 1.0, 48.83),
        ])
        xlsx = make_xlsx_transactions([xlsx_row("2023-01-03", "MCHI", 1.0, 48.83)])
        result = match_trades(sqlite, xlsx)
        assert len(result) == 2
        assert result["matched"].sum() == 1  # only one of the two dupes has a counterpart

    def test_xlsx_rows_with_no_symbol_are_ignored(self):
        # Journal/cash rows in Transactions don't belong in trade matching --
        # same filter build_trade_rows() applies when seeding.
        sqlite = make_sqlite_trades([sqlite_row(1, "2023-01-03", "MCHI", 10, 5.0)])
        xlsx = make_xlsx_transactions([xlsx_row("2023-01-03", None, 10, 5.0)])
        result = match_trades(sqlite, xlsx)
        assert result.iloc[0]["matched"] == False  # noqa: E712

    def test_nan_price_rights_distribution_matches(self):
        sqlite = make_sqlite_trades([sqlite_row(1, "2025-09-25", "UTF.RT", 18.0, None)])
        xlsx = make_xlsx_transactions([xlsx_row("2025-09-25", "UTF.RT", 18.0, None)])
        result = match_trades(sqlite, xlsx)
        assert result.iloc[0]["matched"] == True  # noqa: E712

    def test_empty_sqlite_trades_returns_empty_frame_without_error(self):
        sqlite = make_sqlite_trades([])
        xlsx = make_xlsx_transactions([xlsx_row("2023-01-03", "MCHI", 10, 5.0)])
        result = match_trades(sqlite, xlsx)
        assert result.empty
        assert "matched" in result.columns

    def test_every_input_row_is_preserved(self):
        # Left join contract: unmatched rows must never be silently dropped.
        sqlite = make_sqlite_trades([
            sqlite_row(1, "2023-01-03", "MCHI", 10, 5.0),
            sqlite_row(2, "2023-01-04", "VNAM", 1, 15.48),
        ])
        xlsx = make_xlsx_transactions([xlsx_row("2023-01-03", "MCHI", 10, 5.0)])
        result = match_trades(sqlite, xlsx)
        assert len(result) == 2
        assert set(result["id"]) == {1, 2}


class TestMatchDividendRows:
    def test_grouped_dividend_sum_matches(self):
        # Every real xlsx dividend is a gross row + a negative NRA withholding
        # adjustment row sharing (Trade Date, Symbol) -- must match the sum.
        sqlite = make_sqlite_dividends([sqlite_dividend(1, "2026-06-19", "HDV", "Dividend", 2.09)])
        xlsx = make_xlsx_income([
            xlsx_income_row("2026-06-19", "Dividends", "HDV", 2.45),
            xlsx_income_row("2026-06-19", "Div. Adj(NRA Withheld)", "HDV", -0.36),
        ])
        result = match_dividend_rows(sqlite, xlsx)
        assert result.iloc[0]["matched"] == True  # noqa: E712

    def test_capital_distribution_matches_same_as_dividend(self):
        # No separate xlsx vocabulary for Capital Distribution -- matched on
        # amount alone, same as a regular Dividend row.
        sqlite = make_sqlite_dividends([sqlite_dividend(1, "2026-06-19", "HDV", "Capital Distribution", 2.09)])
        xlsx = make_xlsx_income([
            xlsx_income_row("2026-06-19", "Dividends", "HDV", 2.45),
            xlsx_income_row("2026-06-19", "Div. Adj(NRA Withheld)", "HDV", -0.36),
        ])
        result = match_dividend_rows(sqlite, xlsx)
        assert result.iloc[0]["matched"] == True  # noqa: E712

    def test_interest_rows_excluded_from_this_matcher(self):
        sqlite = make_sqlite_dividends([sqlite_dividend(1, "2026-06-19", None, "Interest", 0.5)])
        xlsx = make_xlsx_income([xlsx_income_row("2026-06-19", "Credit/Margin Interest", "SHV", 0.5)])
        result = match_dividend_rows(sqlite, xlsx)
        assert result.empty

    def test_amount_rounds_to_2dp_before_comparing(self):
        sqlite = make_sqlite_dividends([sqlite_dividend(1, "2026-06-19", "HDV", "Dividend", 2.09)])
        xlsx = make_xlsx_income([xlsx_income_row("2026-06-19", "Dividends", "HDV", 2.0899999)])
        result = match_dividend_rows(sqlite, xlsx)
        assert result.iloc[0]["matched"] == True  # noqa: E712

    def test_amount_mismatch_is_not_matched(self):
        sqlite = make_sqlite_dividends([sqlite_dividend(1, "2026-06-19", "HDV", "Dividend", 2.09)])
        xlsx = make_xlsx_income([xlsx_income_row("2026-06-19", "Dividends", "HDV", 3.00)])
        result = match_dividend_rows(sqlite, xlsx)
        assert result.iloc[0]["matched"] == False  # noqa: E712

    def test_duplicate_manual_dividend_entries_pair_one_to_one(self):
        sqlite = make_sqlite_dividends([
            sqlite_dividend(1, "2026-06-19", "HDV", "Dividend", 2.09),
            sqlite_dividend(2, "2026-06-19", "HDV", "Dividend", 2.09),
        ])
        xlsx = make_xlsx_income([
            xlsx_income_row("2026-06-19", "Dividends", "HDV", 2.45),
            xlsx_income_row("2026-06-19", "Div. Adj(NRA Withheld)", "HDV", -0.36),
        ])
        result = match_dividend_rows(sqlite, xlsx)
        assert len(result) == 2
        assert result["matched"].sum() == 1  # only one xlsx group exists for this key

    def test_entry_type_not_part_of_the_match_key(self):
        # xlsx/SQLite entry_type vocabularies never line up -- matching goes
        # on (date, symbol, amount) alone, regardless of xlsx row count/shape.
        sqlite = make_sqlite_dividends([sqlite_dividend(1, "2026-06-19", "HDV", "Dividend", 2.09)])
        xlsx = make_xlsx_income([xlsx_income_row("2026-06-19", "Dividends", "HDV", 2.09)])  # no NRA adjustment row
        result = match_dividend_rows(sqlite, xlsx)
        assert result.iloc[0]["matched"] == True  # noqa: E712

    def test_empty_sqlite_dividends_returns_empty_frame_without_error(self):
        sqlite = make_sqlite_dividends([])
        xlsx = make_xlsx_income([xlsx_income_row("2026-06-19", "Dividends", "HDV", 2.09)])
        result = match_dividend_rows(sqlite, xlsx)
        assert result.empty
        assert "matched" in result.columns


class TestMatchInterestRows:
    def test_ignores_xlsx_symbol_entirely(self):
        # seed_from_xlsx.py hardcodes symbol=None for every interest row
        # regardless of what the xlsx shows -- matching must not require it.
        sqlite = make_sqlite_dividends([sqlite_dividend(1, "2024-04-05", None, "Interest", 0.72)])
        xlsx = make_xlsx_income([xlsx_income_row("2024-04-05", "Credit/Margin Interest", "SHV", 0.72)])
        result = match_interest_rows(sqlite, xlsx)
        assert result.iloc[0]["matched"] == True  # noqa: E712

    def test_same_day_multi_symbol_interest_pairs_positionally(self):
        # Real case: 2024-04-05 posts SGOV 0.14 + SHV 0.72 as two separate
        # un-summed rows -- must pair 1:1, not group/sum into one match.
        sqlite = make_sqlite_dividends([
            sqlite_dividend(1, "2024-04-05", None, "Interest", 0.72),
            sqlite_dividend(2, "2024-04-05", None, "Interest", 0.14),
        ])
        xlsx = make_xlsx_income([
            xlsx_income_row("2024-04-05", "Credit/Margin Interest", "SHV", 0.72),
            xlsx_income_row("2024-04-05", "Credit/Margin Interest", "SGOV", 0.14),
        ])
        result = match_interest_rows(sqlite, xlsx)
        assert result["matched"].sum() == 2

    def test_non_interest_rows_excluded_from_this_matcher(self):
        sqlite = make_sqlite_dividends([sqlite_dividend(1, "2026-06-19", "HDV", "Dividend", 2.09)])
        xlsx = make_xlsx_income([xlsx_income_row("2026-06-19", "Dividends", "HDV", 2.09)])
        result = match_interest_rows(sqlite, xlsx)
        assert result.empty

    def test_amount_mismatch_is_not_matched(self):
        sqlite = make_sqlite_dividends([sqlite_dividend(1, "2024-04-05", None, "Interest", 0.72)])
        xlsx = make_xlsx_income([xlsx_income_row("2024-04-05", "Credit/Margin Interest", "SHV", 0.71)])
        result = match_interest_rows(sqlite, xlsx)
        assert result.iloc[0]["matched"] == False  # noqa: E712

    def test_empty_sqlite_dividends_returns_empty_frame_without_error(self):
        sqlite = make_sqlite_dividends([])
        xlsx = make_xlsx_income([xlsx_income_row("2024-04-05", "Credit/Margin Interest", "SHV", 0.72)])
        result = match_interest_rows(sqlite, xlsx)
        assert result.empty
        assert "matched" in result.columns


class TestMatchDividends:
    def test_covers_every_input_row_exactly_once(self):
        # Entry Type is a strict two-way partition (Interest vs. everything
        # else) -- every input row must appear in the combined result once.
        sqlite = make_sqlite_dividends([
            sqlite_dividend(1, "2026-06-19", "HDV", "Dividend", 2.09),
            sqlite_dividend(2, "2024-04-05", None, "Interest", 0.72),
        ])
        xlsx = make_xlsx_income([
            xlsx_income_row("2026-06-19", "Dividends", "HDV", 2.09),
            xlsx_income_row("2024-04-05", "Credit/Margin Interest", "SHV", 0.72),
        ])
        result = match_dividends(sqlite, xlsx)
        assert len(result) == 2
        assert set(result["id"]) == {1, 2}
        assert result["matched"].all()


class TestUnmatchedXlsxTrades:
    def test_detects_xlsx_trade_never_logged(self):
        sqlite_all = make_sqlite_trades([sqlite_row(1, "2023-01-03", "MCHI", 1.0, 48.83)])
        xlsx = make_xlsx_transactions([
            xlsx_row("2023-01-03", "MCHI", 1.0, 48.83),
            xlsx_row("2023-01-04", "VNAM", 1.0, 15.48),  # never logged
        ])
        result = unmatched_xlsx_trades(sqlite_all, xlsx)
        assert len(result) == 1
        assert result.iloc[0]["Symbol"] == "VNAM"

    def test_no_gaps_when_everything_is_logged(self):
        sqlite_all = make_sqlite_trades([sqlite_row(1, "2023-01-03", "MCHI", 1.0, 48.83)])
        xlsx = make_xlsx_transactions([xlsx_row("2023-01-03", "MCHI", 1.0, 48.83)])
        result = unmatched_xlsx_trades(sqlite_all, xlsx)
        assert result.empty

    def test_already_reconciled_row_still_counts_as_logged(self):
        # unmatched_xlsx_trades doesn't filter on reconciled_month itself --
        # it's on the caller to pass the FULL trades table, not just
        # unreconciled candidates. A previously-reconciled row is still a
        # real SQLite counterpart and must not surface as a fresh gap here.
        sqlite_all = make_sqlite_trades([sqlite_row(1, "2023-01-03", "MCHI", 1.0, 48.83)])
        sqlite_all["reconciled_month"] = "2023-01"
        xlsx = make_xlsx_transactions([xlsx_row("2023-01-03", "MCHI", 1.0, 48.83)])
        result = unmatched_xlsx_trades(sqlite_all, xlsx)
        assert result.empty

    def test_since_filters_out_older_gaps(self):
        sqlite_all = make_sqlite_trades([])
        xlsx = make_xlsx_transactions([
            xlsx_row("2023-01-03", "MCHI", 1.0, 48.83),
            xlsx_row("2026-06-01", "VNAM", 1.0, 15.48),
        ])
        result = unmatched_xlsx_trades(sqlite_all, xlsx, since=pd.Timestamp("2026-01-01"))
        assert len(result) == 1
        assert result.iloc[0]["Symbol"] == "VNAM"

    def test_xlsx_rows_with_no_symbol_are_ignored(self):
        sqlite_all = make_sqlite_trades([])
        xlsx = make_xlsx_transactions([xlsx_row("2023-01-03", None, 1.0, 48.83)])
        result = unmatched_xlsx_trades(sqlite_all, xlsx)
        assert result.empty

    def test_empty_since_filtered_xlsx_returns_empty_frame_without_error(self):
        sqlite_all = make_sqlite_trades([])
        xlsx = make_xlsx_transactions([xlsx_row("2023-01-03", "MCHI", 1.0, 48.83)])
        result = unmatched_xlsx_trades(sqlite_all, xlsx, since=pd.Timestamp("2030-01-01"))
        assert result.empty


class TestUnmatchedXlsxIncome:
    def test_detects_xlsx_dividend_never_logged(self):
        sqlite_all = make_sqlite_dividends([])
        xlsx = make_xlsx_income([
            xlsx_income_row("2026-06-19", "Dividends", "HDV", 2.45),
            xlsx_income_row("2026-06-19", "Div. Adj(NRA Withheld)", "HDV", -0.36),
        ])
        result = unmatched_xlsx_income(sqlite_all, xlsx)
        assert len(result) == 1
        assert result.iloc[0]["Symbol"] == "HDV"
        assert result.iloc[0]["Net Amt"] == pytest.approx(2.09)

    def test_detects_xlsx_interest_never_logged(self):
        sqlite_all = make_sqlite_dividends([])
        xlsx = make_xlsx_income([xlsx_income_row("2024-04-05", "Credit/Margin Interest", "SHV", 0.72)])
        result = unmatched_xlsx_income(sqlite_all, xlsx)
        assert len(result) == 1
        assert result.iloc[0]["Entry Type"] == "Interest"
        assert result.iloc[0]["Symbol"] == "SHV"  # informational -- real xlsx symbol shown even though not matched on

    def test_no_gaps_when_everything_is_logged(self):
        sqlite_all = make_sqlite_dividends([sqlite_dividend(1, "2026-06-19", "HDV", "Dividend", 2.09)])
        xlsx = make_xlsx_income([
            xlsx_income_row("2026-06-19", "Dividends", "HDV", 2.45),
            xlsx_income_row("2026-06-19", "Div. Adj(NRA Withheld)", "HDV", -0.36),
        ])
        result = unmatched_xlsx_income(sqlite_all, xlsx)
        assert result.empty

    def test_since_filters_out_older_gaps(self):
        sqlite_all = make_sqlite_dividends([])
        xlsx = make_xlsx_income([
            xlsx_income_row("2023-01-03", "Dividends", "HDV", 1.0),
            xlsx_income_row("2026-06-19", "Dividends", "AAA", 2.0),
        ])
        result = unmatched_xlsx_income(sqlite_all, xlsx, since=pd.Timestamp("2026-01-01"))
        assert len(result) == 1
        assert result.iloc[0]["Symbol"] == "AAA"

    def test_output_columns_are_uniform_across_dividend_and_interest_gaps(self):
        sqlite_all = make_sqlite_dividends([])
        xlsx = make_xlsx_income([
            xlsx_income_row("2026-06-19", "Dividends", "HDV", 2.45),
            xlsx_income_row("2026-06-19", "Div. Adj(NRA Withheld)", "HDV", -0.36),
            xlsx_income_row("2024-04-05", "Credit/Margin Interest", "SHV", 0.72),
        ])
        result = unmatched_xlsx_income(sqlite_all, xlsx)
        assert len(result) == 2
        assert list(result.columns) == ["Trade Date", "Symbol", "Entry Type", "Net Amt"]


class TestLoadXlsxForReconciliation:
    def test_loads_real_workbook_and_computes_expected_cutoff(self):
        cutoff, transactions, income = load_xlsx_for_reconciliation(
            "data/Offshore_Statements_2023-01_to_2026-06.xlsx"
        )
        assert cutoff == pd.Timestamp("2026-06-30")
        assert len(transactions) > 0
        assert len(income) > 0
        assert pd.api.types.is_datetime64_any_dtype(transactions["Trade Date"])

    def test_real_data_trade_match_rate_is_near_complete(self):
        # Concrete, falsifiable expectation from the plan's research: matching
        # every real Transactions row (Symbol.notna()) against itself should
        # be ~100% -- this is the same data seeded into SQLite via
        # scripts/seed_from_xlsx.py, so it's a self-consistency check on the
        # matcher, not a claim about live SQLite data (that's Step 5's job).
        _, transactions, _ = load_xlsx_for_reconciliation("data/Offshore_Statements_2023-01_to_2026-06.xlsx")
        tx = transactions[transactions["Symbol"].notna()].reset_index(drop=True)
        sqlite_like = tx.rename(columns={}).copy()
        sqlite_like["id"] = range(len(sqlite_like))
        result = match_trades(sqlite_like[["id", "Trade Date", "Symbol", "Quantity", "Price"]], transactions)
        assert len(result) == 902
        assert result["matched"].sum() == 902

    def test_real_data_dividend_match_rate_is_near_complete(self):
        # Same self-consistency idea as the trades check above, mirroring
        # scripts/seed_from_xlsx.py::build_dividend_rows()'s own grouping
        # logic: 878 grouped dividend rows + 17 interest rows = 895 total,
        # the plan's predicted figure.
        _, _, income = load_xlsx_for_reconciliation("data/Offshore_Statements_2023-01_to_2026-06.xlsx")

        div = income[income["Entry Type"].isin(["Dividends", "Div. Adj(NRA Withheld)"]) & income["Symbol"].notna()]
        grouped = div.groupby(["Trade Date", "Symbol"], as_index=False)["Net Amt"].sum()
        grouped["id"] = range(len(grouped))
        grouped["Entry Type"] = "Dividend"

        interest = income[income["Entry Type"] == "Credit/Margin Interest"].dropna(subset=["Trade Date", "Net Amt"]).copy()
        interest["id"] = range(len(grouped), len(grouped) + len(interest))
        interest["Symbol"] = None
        interest["Entry Type"] = "Interest"

        sqlite_like = pd.concat([
            grouped[["id", "Trade Date", "Symbol", "Entry Type", "Net Amt"]],
            interest[["id", "Trade Date", "Symbol", "Entry Type", "Net Amt"]],
        ], ignore_index=True)

        result = match_dividends(sqlite_like, income)
        assert len(result) == 895
        assert result["matched"].sum() == 895
