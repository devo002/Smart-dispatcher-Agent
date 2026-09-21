"""check_inventory: Sheets cache TTL, stale fallback, row parsing, query + summary logic."""

import pytest

import importlib

# tools/__init__ re-exports a function named check_inventory, which shadows the module.
ci = importlib.import_module("src.empire_dispatcher.tools.check_inventory")

from src.empire_dispatcher.tools.check_inventory import (
    CheckInventoryInput,
    InventoryRow,
    check_inventory,
)


def _row(part_id="P-1", name="Widget", stock=5, status="active", wh="Berlin-Tempelhof"):
    return InventoryRow(
        part_id=part_id, part_name=name, manufacturer="Huawei", compatible_models="X",
        stock_level=stock, warehouse_location=wh, price_eur=10.0, status=status,
    )


def _record(**over):
    r = {
        "part_id": " P-1 ", "part_name": "Widget", "manufacturer": "Huawei",
        "compatible_models": "X", "stock_level": "5", "warehouse_location": "Berlin-Tempelhof",
        "price_eur": "10.5", "status": "active",
    }
    r.update(over)
    return r


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(ci, "_sheet_cache", {"rows": None, "fetched_at": 0.0})
    monkeypatch.setattr(ci.time, "sleep", lambda _s: None)  # no real retry backoff


def _fake_gspread(monkeypatch, records=None, fail=False):
    """Install a fake gspread; returns a counter of get_all_records() calls."""
    import sys
    import types

    calls = {"n": 0}

    class WS:
        def get_all_records(self):
            calls["n"] += 1
            if fail:
                raise RuntimeError("sheets down")
            return records

    class SH:
        def worksheet(self, _name):
            return WS()

    class GC:
        def open_by_key(self, _key):
            return SH()

    mod = types.ModuleType("gspread")
    mod.service_account = lambda filename: GC()
    monkeypatch.setitem(sys.modules, "gspread", mod)
    return calls


def _clock(monkeypatch, start=1000.0):
    t = {"now": start}
    monkeypatch.setattr(ci.time, "monotonic", lambda: t["now"])
    return t


# --- Sheets fetch / cache ---------------------------------------------------

def test_fetch_parses_rows_and_strips_whitespace(monkeypatch):
    _clock(monkeypatch)
    _fake_gspread(monkeypatch, [_record()])
    rows = ci._fetch_from_sheets()
    assert rows[0].part_id == "P-1"
    assert rows[0].stock_level == 5 and rows[0].price_eur == 10.5


def test_fetch_skips_malformed_rows(monkeypatch):
    _clock(monkeypatch)
    _fake_gspread(monkeypatch, [_record(), _record(stock_level="not-a-number"), {"part_id": "only"}])
    assert len(ci._fetch_from_sheets()) == 1


def test_cache_hit_within_ttl_does_not_refetch(monkeypatch):
    t = _clock(monkeypatch)
    calls = _fake_gspread(monkeypatch, [_record()])
    ci._fetch_from_sheets()
    t["now"] += ci._SHEET_CACHE_TTL - 1
    ci._fetch_from_sheets()
    assert calls["n"] == 1


def test_refetch_after_ttl_expires(monkeypatch):
    t = _clock(monkeypatch)
    calls = _fake_gspread(monkeypatch, [_record()])
    ci._fetch_from_sheets()
    t["now"] += ci._SHEET_CACHE_TTL + 1
    ci._fetch_from_sheets()
    assert calls["n"] == 2


def test_failed_fetch_retries_then_serves_stale_rows(monkeypatch):
    t = _clock(monkeypatch)
    _fake_gspread(monkeypatch, [_record()])
    good = ci._fetch_from_sheets()

    t["now"] += ci._SHEET_CACHE_TTL + 1  # cache expired, but inside the stale window
    calls = _fake_gspread(monkeypatch, fail=True)
    assert ci._fetch_from_sheets() == good
    assert calls["n"] == ci._SHEET_FETCH_ATTEMPTS


def test_failed_fetch_past_stale_window_returns_none(monkeypatch):
    t = _clock(monkeypatch)
    _fake_gspread(monkeypatch, [_record()])
    ci._fetch_from_sheets()

    t["now"] += ci._SHEET_STALE_TTL + 1
    _fake_gspread(monkeypatch, fail=True)
    assert ci._fetch_from_sheets() is None


def test_failed_fetch_with_no_cache_returns_none(monkeypatch):
    _clock(monkeypatch)
    _fake_gspread(monkeypatch, fail=True)
    assert ci._fetch_from_sheets() is None


# --- Backend selection -------------------------------------------------------

def test_falls_back_to_sqlite_when_sheets_unreachable(monkeypatch):
    monkeypatch.setattr(ci, "_sheets_configured", lambda: True)
    monkeypatch.setattr(ci, "_fetch_from_sheets", lambda: None)
    monkeypatch.setattr(ci, "_query_sqlite", lambda p: [_row()])
    rows, source = ci._query_rows(CheckInventoryInput(part_id="P-1"))
    assert source == "sqlite" and len(rows) == 1


def test_uses_sheets_when_configured(monkeypatch):
    monkeypatch.setattr(ci, "_sheets_configured", lambda: True)
    monkeypatch.setattr(ci, "_fetch_from_sheets", lambda: [_row()])
    _, source = ci._query_rows(CheckInventoryInput(part_id="P-1"))
    assert source == "google_sheets"


# --- Query + summary logic ---------------------------------------------------

def _use_rows(monkeypatch, rows):
    monkeypatch.setattr(ci, "_query_rows", lambda p: (list(rows), "google_sheets"))


def test_no_part_id_or_query_returns_empty():
    out = check_inventory(CheckInventoryInput())
    assert out.matches == [] and "cannot search" in out.summary


def test_part_id_match_is_case_insensitive():
    rows = [_row("HUA-1"), _row("HUA-2")]
    got = ci._query_sheet_rows(CheckInventoryInput(part_id="hua-2"), rows)
    assert [r.part_id for r in got] == ["HUA-2"]


def test_query_matches_name_sorted_by_stock_and_capped_at_8():
    rows = [_row(f"P-{i}", "Isolator", stock=i) for i in range(12)]
    got = ci._query_sheet_rows(CheckInventoryInput(query="isolator"), rows)
    assert len(got) == 8 and got[0].stock_level == 11


def test_no_matches_summary(monkeypatch):
    _use_rows(monkeypatch, [])
    out = check_inventory(CheckInventoryInput(part_id="NOPE"))
    assert out.matches == [] and "No inventory matches" in out.summary


@pytest.mark.parametrize("stock,status,expected", [
    (5, "active", "in stock"),
    (0, "discontinued", "DISCONTINUED"),
    (0, "backorder", "BACKORDER"),
    (3, "workshop_only", "workshop-only"),
    (0, "active", "out of stock"),
])
def test_summary_reflects_stock_status(monkeypatch, stock, status, expected):
    _use_rows(monkeypatch, [_row(stock=stock, status=status)])
    out = check_inventory(CheckInventoryInput(part_id="P-1"))
    assert expected in out.summary


def test_in_stock_requires_active_status_and_stock():
    assert _row(stock=5, status="active").in_stock
    assert not _row(stock=5, status="backorder").in_stock
    assert not _row(stock=0, status="active").in_stock


def test_region_prefers_closest_warehouse(monkeypatch):
    _use_rows(monkeypatch, [
        _row("P-B", stock=50, wh="Berlin-Tempelhof"),
        _row("P-M", stock=5, wh="Munich-Riem"),
    ])
    out = check_inventory(CheckInventoryInput(query="widget", region="Munich"))
    assert out.matches[0].part_id == "P-M"


def test_unknown_region_keeps_stock_order(monkeypatch):
    _use_rows(monkeypatch, [_row("P-B", stock=50), _row("P-M", stock=5, wh="Munich-Riem")])
    out = check_inventory(CheckInventoryInput(query="widget", region="Atlantis"))
    assert out.matches[0].part_id == "P-B"


def test_sqlite_fallback_reads_real_csv(monkeypatch):
    """Integration-lite: real CSV -> SQLite mirror, no Sheets."""
    monkeypatch.setattr(ci, "_sheets_configured", lambda: False)
    out = check_inventory(CheckInventoryInput(part_id="HUA-AC-ISO-V2"))
    assert out.matches and out.matches[0].status == "discontinued"
    assert "source=sqlite" in out.summary
