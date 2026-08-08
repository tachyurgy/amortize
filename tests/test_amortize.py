"""Conservation tests.

Two properties: a commitment's daily slices sum to what was paid, and
attributing those slices to consuming accounts does not change the total.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from finops.amortize import (
    AmortizationError,
    Commitment,
    Usage,
    attribute,
    daily_slices,
    reconcile,
    spread,
)

JAN = date(2026, 1, 1)


def commitment(upfront=180_000_000_000, days=365, family="m7i", payer="platform"):
    return Commitment(
        id="ri-1",
        payer_account=payer,
        upfront_micros=upfront,
        start=JAN,
        end=JAN + timedelta(days=days - 1),
        instance_family=family,
    )


# -- spread ---------------------------------------------------------------


def test_spread_conserves_exactly():
    for total in range(0, 2000):
        for n in (1, 3, 7, 30, 365):
            assert sum(spread(total, n)) == total, f"{total} across {n}"


def test_spread_parts_differ_by_at_most_one():
    parts = spread(1_000_000, 365)
    assert max(parts) - min(parts) <= 1


def test_spread_is_deterministic():
    assert spread(999_983, 365) == spread(999_983, 365)


def test_spread_rejects_zero_periods():
    with pytest.raises(AmortizationError):
        spread(100, 0)


# -- daily slices ---------------------------------------------------------


def test_daily_slices_sum_to_the_upfront_amount():
    """A prime-ish upfront over 365 days is the case naive division loses."""
    for upfront in (180_000_000_000, 1, 999_999_999, 123_456_789):
        c = commitment(upfront=upfront)
        slices = daily_slices(c)
        assert len(slices) == 365
        assert sum(slices.values()) == upfront


def test_leap_year_term_conserves():
    c = Commitment("ri-leap", "platform", 366_000_000_001, date(2028, 1, 1), date(2028, 12, 31), "m7i")
    assert c.days == 366
    assert sum(daily_slices(c).values()) == 366_000_000_001


# -- attribution (the invariant) ------------------------------------------


def usage_for(days, accounts, family="m7i", units=1.0):
    return [
        Usage(JAN + timedelta(days=d), acct, family, units)
        for d in range(days)
        for acct in accounts
    ]


def test_attribution_conserves_the_purchase():
    """THE invariant: reattributing spend must not create or destroy any."""
    c = commitment()
    ledger = attribute([c], usage_for(365, ["team-a", "team-b", "team-c"]))
    r = reconcile([c], ledger)
    assert r["balanced"], r
    assert r["attributed_micros"] == c.upfront_micros


def test_attribution_conserves_with_uneven_usage():
    c = commitment()
    rng = random.Random(11)
    usage = []
    for d in range(365):
        for acct in ["team-a", "team-b", "team-c", "team-d"]:
            u = rng.choice([0, 0.25, 1, 3.5, 17])
            if u:
                usage.append(Usage(JAN + timedelta(days=d), acct, "m7i", u))
    ledger = attribute([c], usage)
    assert reconcile([c], ledger)["balanced"]


def test_unused_commitment_stays_with_the_payer():
    """Waste must not be redistributed onto consumers -- that would make coverage
    look perfect and the waste number read zero."""
    c = commitment()
    ledger = attribute([c], usage_for(100, ["team-a"]))  # 265 days unused
    assert reconcile([c], ledger)["balanced"]
    assert ledger.unused() > 0
    by_account = ledger.by_account()
    assert by_account["platform"] == ledger.unused()


def test_fully_unused_commitment_is_all_waste():
    c = commitment()
    ledger = attribute([c], [])
    r = reconcile([c], ledger)
    assert r["balanced"]
    assert r["unused_micros"] == c.upfront_micros
    assert r["coverage"] == 0.0


def test_usage_of_a_different_family_does_not_cover():
    c = commitment(family="m7i")
    ledger = attribute([c], usage_for(365, ["team-a"], family="c7g"))
    assert reconcile([c], ledger)["unused_micros"] == c.upfront_micros


def test_each_day_is_conserved_not_just_the_total():
    """A per-day check catches errors that cancel out across the term."""
    c = commitment()
    ledger = attribute([c], usage_for(365, ["team-a", "team-b", "team-c"]))
    slices = daily_slices(c)
    for day, micros in ledger.by_day().items():
        assert micros == slices[day], f"day {day} lost or gained micros"


def test_many_commitments_conserve_together():
    cs = [
        commitment(upfront=180_000_000_000, family="m7i"),
        Commitment("ri-2", "platform", 77_777_777, JAN, JAN + timedelta(days=89), "c7g"),
        Commitment("ri-3", "data", 5, JAN, JAN + timedelta(days=364), "r7i"),
    ]
    usage = (
        usage_for(365, ["team-a", "team-b"], "m7i")
        + usage_for(90, ["team-c"], "c7g")
        + usage_for(200, ["team-a"], "r7i")
    )
    assert reconcile(cs, attribute(cs, usage))["balanced"]


def test_tiny_commitment_across_a_long_term():
    """5 micro-units over 365 days: most days get nothing and the total must
    still be exactly 5."""
    c = Commitment("ri-tiny", "platform", 5, JAN, JAN + timedelta(days=364), "m7i")
    ledger = attribute([c], usage_for(365, ["team-a", "team-b"]))
    assert reconcile([c], ledger)["attributed_micros"] == 5


def test_conservation_holds_on_randomised_scenarios():
    rng = random.Random(20260807)
    for _ in range(200):
        term = rng.randint(1, 400)
        c = Commitment(
            "ri", "payer", rng.randint(0, 10**11), JAN, JAN + timedelta(days=term - 1), "m7i"
        )
        usage = [
            Usage(JAN + timedelta(days=d), f"t{a}", "m7i", rng.random() * 10)
            for d in range(term)
            for a in range(rng.randint(0, 4))
        ]
        assert reconcile([c], attribute([c], usage))["balanced"]


# -- guards ---------------------------------------------------------------


def test_negative_usage_is_refused():
    with pytest.raises(AmortizationError):
        attribute([commitment()], [Usage(JAN, "team-a", "m7i", -1.0)])


def test_backwards_term_is_refused():
    bad = Commitment("x", "p", 100, date(2026, 6, 1), date(2026, 1, 1), "m7i")
    with pytest.raises(AmortizationError):
        attribute([bad], [])


def test_reconcile_reports_an_imbalance_when_there_is_one():
    """The reconciler must be able to fail, or a balanced report proves nothing."""
    c = commitment()
    ledger = attribute([c], usage_for(365, ["team-a"]))
    ledger.charges.pop()
    assert not reconcile([c], ledger)["balanced"]
