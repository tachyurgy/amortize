"""Amortising committed cloud spend across a billing period.

A Reserved Instance or Savings Plan is billed as one upfront charge, often on
day one of a term that runs for a year. Showback that reports what the invoice
says will tell one team it spent $180,000 in January and nothing for the next
eleven months. That is technically what happened and completely useless for
anyone trying to run a budget, so the number every FinOps tool actually reports
is amortised: the commitment spread across the days it covers, and attributed to
whoever used it.

Two rules the rest of the reporting depends on:

  1. **Amortisation conserves.** The daily slices of a commitment sum to exactly
     what was paid for it. Not approximately -- exactly, in integer
     hundredths-of-a-cent. Cloud bills run to seven significant figures and a
     per-day rounding error multiplied by 365 days and a few thousand resources
     is a visible discrepancy against the invoice.

  2. **Reattribution conserves.** Moving spend from the account that bought the
     commitment onto the accounts that consumed it must not change the total.
     Every dollar that leaves one bucket lands in another, and unused commitment
     stays with the buyer rather than evaporating.

Money is integer micro-units (hundredths of a cent) throughout. AWS reports cost
to 10 decimal places; floats lose that quietly.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

MICROS = 10_000  # micro-units per dollar-cent unit; 1 dollar = 1_000_000


class AmortizationError(Exception):
    pass


@dataclass(frozen=True)
class Commitment:
    """An upfront purchase covering a term."""

    id: str
    payer_account: str
    upfront_micros: int
    start: date
    end: date  # inclusive
    instance_family: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass(frozen=True)
class Usage:
    """One account's consumption of a family on a day, in normalised units."""

    day: date
    account: str
    instance_family: str
    units: float


@dataclass
class DailyCharge:
    day: date
    account: str
    commitment_id: str
    micros: int
    kind: str  # "covered" or "unused"


@dataclass
class Ledger:
    charges: list[DailyCharge] = field(default_factory=list)

    def total(self) -> int:
        return sum(c.micros for c in self.charges)

    def by_account(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for c in self.charges:
            out[c.account] += c.micros
        return dict(out)

    def by_day(self) -> dict[date, int]:
        out: dict[date, int] = defaultdict(int)
        for c in self.charges:
            out[c.day] += c.micros
        return dict(out)

    def unused(self) -> int:
        return sum(c.micros for c in self.charges if c.kind == "unused")


def spread(total_micros: int, n: int) -> list[int]:
    """Split `total_micros` into `n` parts that sum to exactly the total.

    Largest remainder. The parts differ by at most one micro-unit, and the
    remainder goes to the earliest days -- an arbitrary but fixed choice, so the
    same commitment always amortises identically. A report that changes when
    recomputed is worse than one that is slightly uneven.
    """
    if n <= 0:
        raise AmortizationError(f"cannot spread across {n} periods")
    base, remainder = divmod(total_micros, n)
    return [base + (1 if i < remainder else 0) for i in range(n)]


def daily_slices(commitment: Commitment) -> dict[date, int]:
    """The commitment's cost per day, summing to the upfront amount."""
    parts = spread(commitment.upfront_micros, commitment.days)
    return {commitment.start + timedelta(days=i): parts[i] for i in range(commitment.days)}


def attribute(
    commitments: list[Commitment],
    usage: list[Usage],
) -> Ledger:
    """Amortise each commitment and attribute each day's slice to consumers.

    A day's slice is divided across the accounts that used that instance family
    that day, in proportion to their usage. Where nobody used it, the slice stays
    with the payer as `unused` -- that is real waste and the number a FinOps team
    is usually hunting for, so it must not be silently redistributed onto
    consumers, which would make coverage look perfect and waste look like zero.
    """
    by_day_family: dict[tuple[date, str], list[Usage]] = defaultdict(list)
    for u in usage:
        if u.units < 0:
            raise AmortizationError(f"negative usage for {u.account} on {u.day}")
        if u.units:
            by_day_family[(u.day, u.instance_family)].append(u)

    ledger = Ledger()
    for c in commitments:
        if c.end < c.start:
            raise AmortizationError(f"commitment {c.id} ends before it starts")
        for day, slice_micros in daily_slices(c).items():
            consumers = by_day_family.get((day, c.instance_family), [])
            if not consumers:
                ledger.charges.append(
                    DailyCharge(day, c.payer_account, c.id, slice_micros, "unused")
                )
                continue

            total_units = sum(u.units for u in consumers)
            # Proportional split, then largest-remainder so the day's slice is
            # conserved exactly rather than left a micro-unit short.
            exact = [slice_micros * (u.units / total_units) for u in consumers]
            base = [int(v) for v in exact]
            leftover = slice_micros - sum(base)
            order = sorted(
                range(len(consumers)),
                key=lambda i: (-(exact[i] - base[i]), consumers[i].account),
            )
            for j in range(leftover):
                base[order[j % len(order)]] += 1

            for u, micros in zip(consumers, base):
                ledger.charges.append(DailyCharge(day, u.account, c.id, micros, "covered"))
    return ledger


def reconcile(commitments: list[Commitment], ledger: Ledger) -> dict:
    """Check the ledger against what was actually purchased.

    Returned in the API response rather than only asserted in tests, because the
    honest answer to 'do these numbers tie out' is one the caller should be able
    to see for the exact payload they were handed.
    """
    purchased = sum(c.upfront_micros for c in commitments)
    attributed = ledger.total()
    return {
        "purchased_micros": purchased,
        "attributed_micros": attributed,
        "difference_micros": purchased - attributed,
        "balanced": purchased == attributed,
        "unused_micros": ledger.unused(),
        "coverage": round(1 - (ledger.unused() / purchased), 6) if purchased else 0.0,
    }
