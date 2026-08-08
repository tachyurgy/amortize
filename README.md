# Amortize

Committed cloud spend, spread across the days it covers and attributed to the accounts
that consumed it — without losing a micro-unit.

Live: **https://amortize.levelbrook.com**

A Reserved Instance or Savings Plan arrives as one upfront charge on day one of a term that
runs for a year or three. Showback built on what the invoice says will tell a team it spent
$184,320 in January and nothing for the next eleven months. That is exactly what happened,
and it is useless to anyone running a budget. So every FinOps tool reports amortised cost
instead: the commitment spread over its term, attributed to whoever actually used it.

The live page shows both shapes side by side. Same total, to the micro-unit.

## Two conservation rules

**Amortisation conserves.** A commitment's daily slices sum to exactly what was paid.
Largest-remainder division, in integer micro-units (hundredths of a cent).

This is not fussiness. AWS reports cost to ten decimal places, and a per-day rounding error
multiplied across 365 days, three commitments and a few thousand resources is a visible
discrepancy against the invoice — the kind that gets a report quietly distrusted rather
than loudly debugged. `184_320_000_000 / 365` does not divide evenly, and neither do most
real commitments.

**Reattribution conserves.** Moving spend off the account that bought a commitment and onto
the accounts that consumed it must not change the total. Each day's slice is divided
proportionally by usage, again with largest-remainder so the day itself is conserved, not
just the term.

Conservation is checked **per day**, not only in total, because per-day errors can cancel
out across a term and leave a clean-looking bottom line.

## Unused commitment stays with the payer

The one judgement call worth naming. Where nobody used a family on a given day, that day's
slice is recorded as `unused` against the payer rather than spread across whoever happened
to be running something else.

Redistributing it would be easy and would make every chart look better: coverage would read
100% and waste would read zero. It would also delete the single number a FinOps team is
usually hired to find. The sample data has an `r7i` commitment whose usage stops on day 120
of a 180-day term for exactly this reason.

## Verifying rather than asserting

`reconcile()` re-derives the balance from a finished ledger and is returned in the API
response, so `/api/report` reports the reconciliation of the exact payload it hands you.
It has its own test that removes a charge and confirms the reconciler *fails* — a checker
that cannot fail proves nothing.

## API

```
GET /api/report      amortised ledger, per-commitment coverage, per-account totals, reconciliation
GET /api/unblended   the same spend as the invoice sees it, for comparison
GET /up
```

## Tests

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest tests -q     # 18 tests
```

Conservation is checked exhaustively over small totals and period counts, on leap-year
terms, on a 5-micro-unit commitment spread across 365 days, and over 200 randomised
scenarios.

The invariants were verified red before being kept: replacing largest-remainder with
`round(total / n)` and dropping the leftover redistribution turns twelve tests failing.

## Layout

```
finops/amortize.py   spreading, attribution, reconciliation
finops/main.py       FastAPI surface
tests/               conservation tests
```

## Limits

This models the arithmetic, not the ingestion. There is no Cost and Usage Report parsing, no
Databricks job, and the commitments and usage are synthetic and generated in-process. It
covers all-upfront commitments only — partial-upfront and no-upfront plans carry a recurring
hourly charge alongside the amortised portion, which is a second stream to reconcile. It
also does not model Savings Plans' cross-family applicability or RI size flexibility, both
of which change *which* usage a commitment can cover and would make attribution a matching
problem rather than a proportional split. Blended versus unblended rates in consolidated
billing are likewise out of scope.

MIT.
