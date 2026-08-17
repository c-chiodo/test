"""Custom Query builder: correctness and safety."""

from __future__ import annotations

import pytest

from pims.errors import ValidationError
from pims.services import query


def test_catalogue_lists_sources_and_fields():
    catalogue = query.catalogue()
    keys = {s["key"] for s in catalogue["sources"]}
    assert {"orders", "transactions", "qc", "balances"} <= keys
    orders_source = next(s for s in catalogue["sources"] if s["key"] == "orders")
    assert any(f["name"] == "order_id" for f in orders_source["fields"])


def test_values_are_bound_never_interpolated():
    sql, params = query.build(
        {
            "source": "orders",
            "fields": ["order_id", "plant_code"],
            "filters": [{"field": "plant_code", "operator": "=", "value": "DM"}],
        }
    )
    assert "?" in sql
    assert "DM" not in sql
    assert params == ["DM"]


def test_a_field_that_is_not_in_the_catalogue_is_rejected():
    with pytest.raises(ValidationError):
        query.build({"source": "orders", "fields": ["password_hash"]})


def test_sql_cannot_be_smuggled_through_a_filter_or_sort(admin_user):
    """The legacy builder assembled SQL text; this one only accepts field names."""

    injection = {
        "source": "orders",
        "fields": ["order_id"],
        "filters": [
            {
                "field": "order_reference; DROP TABLE \"order\"; --",
                "operator": "=",
                "value": "x",
            }
        ],
    }
    with pytest.raises(ValidationError):
        query.build(injection)

    with pytest.raises(ValidationError):
        query.build(
            {
                "source": "orders",
                "fields": ["order_id"],
                "sort": [{"field": "order_id", "direction": "ASC; DELETE FROM qc"}],
            }
        )

    # A value that looks like SQL is data, not code.
    result = query.run(
        {
            "source": "orders",
            "fields": ["order_id", "order_reference"],
            "filters": [
                {
                    "field": "order_reference",
                    "operator": "=",
                    "value": "'; DROP TABLE \"order\"; --",
                }
            ],
        },
        admin_user,
    )
    assert result["row_count"] == 0


def test_unknown_source_is_rejected():
    with pytest.raises(ValidationError):
        query.build({"source": "sqlite_master", "fields": ["name"]})


def test_operators_build_the_expected_clauses():
    sql, params = query.build(
        {
            "source": "qc",
            "fields": ["qc_id"],
            "filters": [
                {"field": "test_date", "operator": "BETWEEN", "value": ["2026-01-01", "2026-12-31"]},
                {"field": "plant_code", "operator": "IN", "value": "DM,SC", "logic": "AND"},
                {"field": "sample_number", "operator": "LIKE", "value": "%Q03%", "logic": "AND"},
                {"field": "ffa", "operator": "IS NOT NULL", "logic": "AND"},
            ],
        }
    )
    assert "BETWEEN ? AND ?" in sql
    assert "IN (?, ?)" in sql
    assert "LIKE ?" in sql
    assert "IS NOT NULL" in sql
    assert params == ["2026-01-01", "2026-12-31", "DM", "SC", "%Q03%"]


def test_or_logic_is_honoured():
    sql, _ = query.build(
        {
            "source": "orders",
            "fields": ["order_id"],
            "filters": [
                {"field": "plant_code", "operator": "=", "value": "DM"},
                {"field": "plant_code", "operator": "=", "value": "SC", "logic": "OR"},
            ],
        }
    )
    assert "OR (" in sql


def test_prompts_are_declared_and_required(admin_user):
    definition = {
        "source": "orders",
        "fields": ["order_id", "plant_code"],
        "filters": [
            {"field": "plant_code", "operator": "=", "prompt": True, "prompt_key": "plant"}
        ],
    }
    prompts = query.prompts_for(definition)
    assert prompts == [
        {
            "key": "plant",
            "field": "plant_code",
            "label": "Plant",
            "type": "text",
            "operator": "=",
        }
    ]

    with pytest.raises(ValidationError):
        query.run(definition, admin_user)

    result = query.run(definition, admin_user, {"plant": "DM"})
    assert result["row_count"] > 0
    assert all(row["plant_code"] == "DM" for row in result["rows"])


def test_row_limit_is_capped(admin_user):
    result = query.run(
        {"source": "orders", "fields": ["order_id"], "limit": 99_999}, admin_user
    )
    assert result["row_count"] <= query.MAX_ROWS


def test_the_generated_sql_is_returned_for_support(admin_user):
    result = query.run({"source": "balances", "fields": ["location_number", "balance"]}, admin_user)
    assert result["sql"].startswith("SELECT")
    assert "location_number" in result["sql"]


def test_saved_queries_round_trip(admin_user):
    saved = query.save(
        "Out of spec loads",
        "QC results with an FFA reading",
        {
            "source": "qc",
            "fields": ["qc_id", "sample_number", "ffa"],
            "filters": [{"field": "ffa", "operator": "IS NOT NULL"}],
            "sort": [{"field": "test_date", "direction": "DESC"}],
        },
        admin_user,
    )
    fetched = query.get_saved(saved["query_id"])
    assert fetched["name"] == "Out of spec loads"
    assert fetched["definition"]["source"] == "qc"
    assert saved["query_id"] in {q["query_id"] for q in query.list_saved()}

    query.deactivate(saved["query_id"], admin_user)
    assert saved["query_id"] not in {q["query_id"] for q in query.list_saved()}


def test_csv_export_matches_the_selected_columns(admin_user):
    result = query.run(
        {"source": "orders", "fields": ["order_id", "plant_code", "status"], "limit": 5},
        admin_user,
    )
    csv = query.to_csv(result)
    assert csv.splitlines()[0] == "order_id,plant_code,status"
    assert len(csv.strip().splitlines()) == result["row_count"] + 1
