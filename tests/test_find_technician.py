"""find_technician scoring, run against small in-memory technician tables."""

from datetime import date

import pandas as pd

import importlib

from src.empire_dispatcher.tools.find_technician import FindTechnicianInput, find_technician

# tools/__init__ re-exports a function named find_technician, which shadows the module.
ft = importlib.import_module("src.empire_dispatcher.tools.find_technician")

TODAY = "2026-05-04"


def _df(rows):
    df = pd.DataFrame(rows)
    for col in ("certifications", "skill_tags", "languages"):
        df[col] = df[col].str.split("|")
    return df


def _tech(tech_id, region="Berlin", certs="Huawei", skills="solar", langs="de", avail=TODAY):
    return dict(tech_id=tech_id, name=f"Tech {tech_id}", region=region, certifications=certs,
                skill_tags=skills, languages=langs, next_available=avail)


def _use(monkeypatch, rows):
    monkeypatch.setattr(ft, "_load_techs", lambda: _df(rows))


def _find(**kw):
    kw.setdefault("region", "Berlin")
    kw.setdefault("by_date", TODAY)
    return find_technician(FindTechnicianInput(**kw))


def test_same_region_beats_other_region(monkeypatch):
    _use(monkeypatch, [_tech("A", region="Munich"), _tech("B", region="Berlin")])
    assert _find().matches[0].tech_id == "B"


def test_region_match_is_case_insensitive(monkeypatch):
    _use(monkeypatch, [_tech("A", region="berlin")])
    assert "same region" in _find(region=" BERLIN ").matches[0].rationale


def test_matching_skills_outrank_missing_skills(monkeypatch):
    _use(monkeypatch, [_tech("A", skills="heatpump"), _tech("B", skills="solar|battery")])
    top = _find(required_skills=["solar", "battery"]).matches[0]
    assert top.tech_id == "B" and "skills match" in top.rationale


def test_missing_skill_is_penalised_and_reported(monkeypatch):
    # Same region (+5), missing skill (-4), language (+1): 2.0, versus 9.0 for a tech who has it.
    _use(monkeypatch, [_tech("A", skills="heatpump"), _tech("B", skills="solar")])
    res = _find(required_skills=["solar"])
    assert res.matches[0].tech_id == "B"
    assert "missing skill(s): solar" in res.matches[1].rationale
    assert res.matches[0].score - res.matches[1].score == 7.0  # +3 overlap, -(-4) missing


def test_certifications_boost_and_penalty(monkeypatch):
    _use(monkeypatch, [_tech("A", certs="SMA"), _tech("B", certs="Huawei")])
    res = _find(required_certifications=["Huawei"])
    assert res.matches[0].tech_id == "B"
    assert "missing cert(s): huawei" in res.matches[1].rationale


def test_language_mismatch_penalised(monkeypatch):
    _use(monkeypatch, [_tech("A", langs="en"), _tech("B", langs="de|en")])
    res = _find(preferred_languages=["de"])
    assert res.matches[0].tech_id == "B"
    assert "language mismatch" in res.matches[1].rationale


def test_later_availability_lowers_score(monkeypatch):
    _use(monkeypatch, [_tech("A", avail="2026-05-10"), _tech("B", avail=TODAY)])
    res = _find()
    assert res.matches[0].tech_id == "B"
    assert "next slot in 6d" in res.matches[1].rationale


def test_past_availability_counts_as_today(monkeypatch):
    _use(monkeypatch, [_tech("A", avail="2026-01-01")])
    assert "available today" in _find().matches[0].rationale


def test_unparseable_availability_treated_as_today(monkeypatch):
    _use(monkeypatch, [_tech("A", avail="soon")])
    assert "available today" in _find().matches[0].rationale


def test_returns_at_most_three_sorted_descending(monkeypatch):
    _use(monkeypatch, [_tech(str(i), region="Berlin" if i % 2 else "Munich") for i in range(6)])
    scores = [m.score for m in _find().matches]
    assert len(scores) == 3 and scores == sorted(scores, reverse=True)


def test_no_good_match_summary(monkeypatch):
    _use(monkeypatch, [_tech("A", region="Munich", skills="heatpump")])
    out = _find(required_skills=["solar"])
    assert out.summary.startswith("No good technician match")


def test_good_match_summary_names_best_tech(monkeypatch):
    _use(monkeypatch, [_tech("A")])
    assert _find().summary.startswith("Best match: Tech A (A)")


def test_by_date_defaults_to_today(monkeypatch):
    _use(monkeypatch, [_tech("A", avail=date.today().isoformat())])
    out = find_technician(FindTechnicianInput(region="Berlin"))
    assert "available today" in out.matches[0].rationale


def test_real_csv_loads_and_returns_matches():
    ft._load_techs.cache_clear()
    out = find_technician(FindTechnicianInput(region="Berlin", required_skills=["solar"]))
    assert out.matches and out.matches[0].region == "Berlin"
