"""Pick the best technician for a job based on region, skills, and earliest slot."""

from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache

import pandas as pd
from pydantic import BaseModel, Field

from ..config import settings


class FindTechnicianInput(BaseModel):
    region: str = Field(..., description="Customer region, e.g. 'Berlin' or 'Munich'.")
    required_skills: list[str] = Field(
        default_factory=list,
        description="Tags like 'solar', 'heatpump', 'battery', 'refrigerant', 'brazing'.",
    )
    required_certifications: list[str] = Field(
        default_factory=list,
        description="Optional vendor certs, e.g. 'Huawei', 'SMA', 'Vaillant'.",
    )
    preferred_languages: list[str] = Field(
        default_factory=lambda: ["de"],
        description="ISO codes; technician must speak at least one.",
    )
    by_date: str | None = Field(
        None,
        description="Earliest acceptable date (YYYY-MM-DD). Default: today.",
    )


class TechnicianMatch(BaseModel):
    tech_id: str
    name: str
    region: str
    certifications: list[str]
    skills: list[str]
    languages: list[str]
    next_available: str
    score: float
    rationale: str


class FindTechnicianOutput(BaseModel):
    matches: list[TechnicianMatch]
    summary: str


@lru_cache(maxsize=1)
def _load_techs() -> pd.DataFrame:
    df = pd.read_csv(settings.technicians_csv)
    df["certifications"] = df["certifications"].fillna("").str.split("|")
    df["skill_tags"] = df["skill_tags"].fillna("").str.split("|")
    df["languages"] = df["languages"].fillna("").str.split("|")
    return df


def _score(row, payload: FindTechnicianInput, target: date) -> tuple[float, str]:
    score = 0.0
    reasons: list[str] = []

    if row["region"].strip().lower() == payload.region.strip().lower():
        score += 5.0
        reasons.append("same region")
    else:
        score -= 2.0
        reasons.append(f"different region ({row['region']})")

    skills = {s.lower() for s in row["skill_tags"]}
    needed = {s.lower() for s in payload.required_skills}
    overlap = skills & needed
    if needed:
        score += 3.0 * len(overlap)
        if overlap:
            reasons.append(f"skills match: {', '.join(sorted(overlap))}")
        missing = needed - overlap
        if missing:
            score -= 4.0 * len(missing)
            reasons.append(f"missing skill(s): {', '.join(sorted(missing))}")

    certs = {c.lower() for c in row["certifications"]}
    cneeded = {c.lower() for c in payload.required_certifications}
    coverlap = certs & cneeded
    if cneeded:
        score += 2.5 * len(coverlap)
        if coverlap:
            reasons.append(f"cert match: {', '.join(sorted(coverlap))}")
        cmissing = cneeded - coverlap
        if cmissing:
            score -= 3.0 * len(cmissing)
            reasons.append(f"missing cert(s): {', '.join(sorted(cmissing))}")

    langs = {l.lower() for l in row["languages"]}
    pref = {l.lower() for l in payload.preferred_languages}
    if pref and (langs & pref):
        score += 1.0
        reasons.append("speaks preferred language")
    elif pref:
        score -= 2.0
        reasons.append("language mismatch")

    try:
        avail = datetime.strptime(row["next_available"], "%Y-%m-%d").date()
    except Exception:
        avail = target
    delay = max((avail - target).days, 0)
    score -= 0.5 * delay
    if delay == 0:
        reasons.append("available today")
    else:
        reasons.append(f"next slot in {delay}d")

    return round(score, 2), "; ".join(reasons)


def find_technician(payload: FindTechnicianInput) -> FindTechnicianOutput:
    target = (
        datetime.strptime(payload.by_date, "%Y-%m-%d").date()
        if payload.by_date
        else date.today()
    )
    df = _load_techs().copy()

    scored: list[TechnicianMatch] = []
    for _, row in df.iterrows():
        score, reason = _score(row, payload, target)
        scored.append(
            TechnicianMatch(
                tech_id=row["tech_id"],
                name=row["name"],
                region=row["region"],
                certifications=list(row["certifications"]),
                skills=list(row["skill_tags"]),
                languages=list(row["languages"]),
                next_available=row["next_available"],
                score=score,
                rationale=reason,
            )
        )

    scored.sort(key=lambda m: m.score, reverse=True)
    top = scored[:3]
    if not top or top[0].score < 0:
        summary = (
            f"No good technician match for region={payload.region!r} "
            f"skills={payload.required_skills}. Best candidate scored {top[0].score if top else 'n/a'}."
        )
    else:
        b = top[0]
        summary = (
            f"Best match: {b.name} ({b.tech_id}) in {b.region}, "
            f"available {b.next_available}. Why: {b.rationale}."
        )
    return FindTechnicianOutput(matches=top, summary=summary)
