"""FastAPI surface over the amortisation engine."""
from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .amortize import Commitment, Usage, attribute, reconcile

app = FastAPI(title="Amortize", docs_url="/api/docs")
WEB = Path(__file__).resolve().parent.parent / "web"

START = date(2026, 1, 1)
ACCOUNTS = ["platform", "search", "ingest", "analytics"]

COMMITMENTS = [
    Commitment("ri-m7i-3yr", "platform", 184_320_000_000, START, START + timedelta(days=364), "m7i"),
    Commitment("sp-compute", "platform", 62_450_000_007, START, START + timedelta(days=364), "c7g"),
    Commitment("ri-r7i-mem", "platform", 41_000_000_003, START, START + timedelta(days=179), "r7i"),
]


def sample_usage(seed: int = 3) -> list[Usage]:
    """Synthetic consumption with deliberate gaps, so there is real waste to find."""
    rng = random.Random(seed)
    out: list[Usage] = []
    for d in range(365):
        day = START + timedelta(days=d)
        for family, accounts in (("m7i", ACCOUNTS[1:]), ("c7g", ["search", "ingest"]), ("r7i", ["analytics"])):
            # r7i usage stops two-thirds of the way through its term: an
            # expensive commitment nobody is consuming any more.
            if family == "r7i" and d > 120:
                continue
            if family == "c7g" and 200 < d < 240:
                continue
            for acct in accounts:
                units = max(0.0, rng.gauss(6, 2.5))
                if units:
                    out.append(Usage(day, acct, family, units))
    return out


USAGE = sample_usage()


@app.get("/up")
def up() -> dict:
    return {"status": "ok", "commitments": len(COMMITMENTS)}


@app.get("/api/report")
def report() -> dict:
    ledger = attribute(COMMITMENTS, USAGE)
    rec = reconcile(COMMITMENTS, ledger)

    per_commitment = []
    for c in COMMITMENTS:
        charges = [x for x in ledger.charges if x.commitment_id == c.id]
        unused = sum(x.micros for x in charges if x.kind == "unused")
        per_commitment.append(
            {
                "id": c.id,
                "family": c.instance_family,
                "upfront_micros": c.upfront_micros,
                "days": c.days,
                "attributed_micros": sum(x.micros for x in charges),
                "unused_micros": unused,
                "coverage": round(1 - unused / c.upfront_micros, 4) if c.upfront_micros else 0.0,
                "balanced": sum(x.micros for x in charges) == c.upfront_micros,
            }
        )

    monthly: dict[str, int] = {}
    for charge in ledger.charges:
        key = charge.day.strftime("%Y-%m")
        monthly[key] = monthly.get(key, 0) + charge.micros

    return {
        "reconciliation": rec,
        "by_account": ledger.by_account(),
        "by_commitment": per_commitment,
        "monthly": dict(sorted(monthly.items())),
    }


@app.get("/api/unblended")
def unblended() -> dict:
    """What the invoice looks like before amortisation, for comparison.

    Every commitment lands entirely on its purchase date and on the payer. Same
    total, wildly different shape -- which is the whole reason amortisation
    exists.
    """
    monthly: dict[str, int] = {}
    by_account: dict[str, int] = {}
    for c in COMMITMENTS:
        key = c.start.strftime("%Y-%m")
        monthly[key] = monthly.get(key, 0) + c.upfront_micros
        by_account[c.payer_account] = by_account.get(c.payer_account, 0) + c.upfront_micros
    return {"monthly": monthly, "by_account": by_account,
            "total_micros": sum(c.upfront_micros for c in COMMITMENTS)}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (WEB / "index.html").read_text()
