"""The clock, injected rather than read from the wall.

Several checks depend on today's date — a due date already in the past is a signal, and
one sample invoice says `Due Date: yesterday`. Reading the system clock directly would
make those checks behave differently depending on when the suite runs, so the date
enters through the dependency container like anything else non-deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def today(self) -> date:
        """The business date decisions are made against."""
        ...


class SystemClock:
    def today(self) -> date:
        return date.today()


@dataclass(frozen=True)
class FixedClock:
    """A clock frozen at a chosen date, so date-dependent behaviour is assertable."""

    fixed: date

    def today(self) -> date:
        return self.fixed
