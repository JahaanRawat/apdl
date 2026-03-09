"""Unit tests for SQL query templates and the funnel query builder."""

from app.clickhouse.queries import build_funnel_query


class TestBuildFunnelQuery:
    def test_two_step_funnel(self):
        """A 2-step funnel should reference both events."""
        sql = build_funnel_query(["signup", "purchase"])

        assert "windowFunnel" in sql
        assert "signup" in sql
        assert "purchase" in sql
        assert "%(project_id)s" in sql
        assert "%(start_date)s" in sql
        assert "%(end_date)s" in sql

    def test_three_step_funnel(self):
        """A 3-step funnel should reference all three events."""
        sql = build_funnel_query(["view", "add_to_cart", "checkout"])

        assert "view" in sql
        assert "add_to_cart" in sql
        assert "checkout" in sql

    def test_custom_window_seconds(self):
        """Custom window_seconds should appear in the windowFunnel call."""
        sql = build_funnel_query(["a", "b"], window_seconds=3600)

        assert "3600" in sql

    def test_default_window_is_7_days(self):
        """Default window should be 7 * 86400 = 604800 seconds."""
        sql = build_funnel_query(["a", "b"])

        assert "604800" in sql

    def test_in_clause_contains_all_steps(self):
        """The IN clause should list all step event names for pre-filtering."""
        steps = ["step_a", "step_b", "step_c"]
        sql = build_funnel_query(steps)

        for step in steps:
            # Should appear in both the windowFunnel conditions and the IN clause
            assert sql.count(f"'{step}'") >= 2

    def test_single_step_funnel(self):
        """Even a single-step funnel should produce valid SQL."""
        sql = build_funnel_query(["only_event"])

        assert "windowFunnel" in sql
        assert "only_event" in sql
