"""Regression tests for the Bayesian-damped popularity ranking SQL.

These compile the expression against the real Postgres dialect rather than
executing it, so they need no database — but they still catch the class of bug
that took `/books/trending` and `/recommendations/personalized` down with a
503 while every other test stayed green.

The bug: `prior_mean * prior_votes` is 175, and SQLAlchemy infers an untyped
numeric literal's type from its neighbours in the expression. Its neighbour is
``books.average_rating``, declared ``Numeric(3, 2)`` — maximum 9.99. The
literal bound as NUMERIC(3, 2) and Postgres rejected the query outright with
``numeric field overflow``. Nothing about the Python is wrong, which is why
this needs a test at the compiled-SQL level.
"""

import re

import pytest
from sqlalchemy import Float, Numeric, desc, func, literal, select
from sqlalchemy.dialects import postgresql

from app.models import Book

# The ceiling implied by Numeric(3, 2): three significant digits, two after the
# decimal point.
AVERAGE_RATING_MAX = 9.99

PRIOR_VOTES, PRIOR_MEAN = 50, 3.5
PRIOR_MASS = PRIOR_MEAN * PRIOR_VOTES  # 175.0 — well past AVERAGE_RATING_MAX


def compile_sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": False},
        )
    )


def damped_expression(prior_mass):
    """The ranking used by both call sites, parameterised on the prior mass."""
    return (
        func.coalesce(Book.average_rating, PRIOR_MEAN)
        * func.coalesce(Book.rating_count, 0)
        + prior_mass
    ) / (func.coalesce(Book.rating_count, 0) + PRIOR_VOTES)


def test_average_rating_column_is_the_narrow_type_that_caused_the_bug():
    """Guards the premise: if this widens, the overflow trap is gone."""
    column_type = Book.__table__.c.average_rating.type
    assert isinstance(column_type, Numeric)
    assert (column_type.precision, column_type.scale) == (3, 2)
    assert PRIOR_MASS > AVERAGE_RATING_MAX


def test_untyped_prior_mass_still_reproduces_the_overflow_typing():
    """The original formulation, kept as the thing we are protecting against.

    A bare Python float adopts ``average_rating``'s NUMERIC(3, 2), which is
    exactly what Postgres refused. If SQLAlchemy ever stops doing this, this
    test fails loudly and the explicit cast can be reconsidered.
    """
    sql = compile_sql(select(Book.id).order_by(desc(damped_expression(PRIOR_MASS))))
    assert re.search(r"NUMERIC\(3,\s*2\)", sql), (
        "expected the untyped literal to inherit NUMERIC(3, 2); "
        f"got:\n{sql}"
    )


@pytest.mark.parametrize("label", ["trending", "popular_books"])
def test_typed_prior_mass_never_binds_as_the_narrow_numeric(label):
    """The fix: an explicitly typed literal compiles to FLOAT, not NUMERIC(3, 2)."""
    prior_mass = literal(PRIOR_MASS, Float)
    statement = select(Book.id).order_by(desc(damped_expression(prior_mass)))
    if label == "popular_books":
        statement = (
            select(Book.id, damped_expression(prior_mass).label("score"))
            .where(Book.embedding.is_not(None))
            .order_by(desc("score"))
        )

    sql = compile_sql(statement)
    assert not re.search(r"NUMERIC\(3,\s*2\)", sql), (
        f"prior mass bound as NUMERIC(3, 2) — {PRIOR_MASS} overflows its "
        f"9.99 ceiling and Postgres 503s the endpoint:\n{sql}"
    )
    assert "FLOAT" in sql.upper()


def test_endpoint_and_engine_both_use_the_typed_literal():
    """Both call sites must carry the fix — they were duplicated, not shared."""
    from pathlib import Path

    sources = {
        "books endpoint": Path("app/api/v1/endpoints/books.py"),
        "recommendation engine": Path("app/services/ai/recommendation_engine.py"),
    }
    for name, path in sources.items():
        text = path.read_text(encoding="utf-8")
        assert "literal(prior_mean * prior_votes, Float)" in text, (
            f"{name} ({path}) computes the damped ranking without the typed "
            "literal; it will 503 with 'numeric field overflow'."
        )
