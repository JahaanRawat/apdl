from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SQL = (
    ROOT
    / "pipeline"
    / "postgres"
    / "migrations"
    / "001_initial_schema.sql"
).read_text()


def test_completeness_baseline_has_strict_tenant_scoped_authorities():
    assert "CREATE TABLE public.event_pipeline_watermarks" in SQL
    assert "event_pipeline_watermarks_pkey PRIMARY KEY (project_id)" in SQL
    assert "stream_key = ('events:raw:'::text || project_id)" in SQL
    assert "CREATE TABLE public.experiment_analysis_boundaries" in SQL
    assert "experiment_analysis_boundaries_pkey PRIMARY KEY" in SQL
    assert "CREATE TABLE public.experiment_analysis_snapshots" in SQL
    assert "data_completeness'::text) = 'verified'::text" in SQL


def test_completeness_authorities_fail_closed_and_snapshots_are_immutable():
    assert "status = 'healthy'::text" in SQL
    assert "status = 'degraded'" in SQL
    assert "legacy_state_unverifiable" in SQL
    assert "dead_lettered_event" in SQL
    assert "lost_pending_entry" in SQL
    assert "experiment_analysis_boundaries_immutable" in SQL
    assert "experiment_analysis_snapshots_immutable" in SQL
    assert "experiment analysis snapshots are immutable" in SQL
    assert "event_pipeline_watermarks_monotonic" in SQL
    assert "event pipeline watermark cannot move backwards" in SQL
    assert "event pipeline degradation is irreversible" in SQL
    assert "event_pipeline_watermarks_no_truncate" in SQL
    assert "experiment_analysis_boundaries_no_truncate" in SQL
    assert "experiment_analysis_snapshots_no_truncate" in SQL


def test_snapshot_foreign_key_includes_the_exact_boundary_stream_id():
    assert "experiment_analysis_boundaries_marker_identity UNIQUE" in SQL
    assert (
        "FOREIGN KEY (project_id, experiment_key, config_version, boundary_stream_id) "
        "REFERENCES public.experiment_analysis_boundaries(project_id, experiment_key, "
        "config_version, marker_stream_id)"
    ) in SQL


def test_stream_id_constraints_match_clickhouse_unsigned_components():
    assert SQL.count("<= '18446744073709551615'::numeric") == 10
    assert "event_pipeline_watermarks_range_check" in SQL
