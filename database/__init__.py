"""SQLite persistence for DiscordServerAudit.

Split into focused submodules; every name below is re-exported so callers keep using
``database.<name>()`` exactly as before. All SQL lives in this package.

NOTE: ``DB_PATH`` below is a snapshot of ``connection.DB_PATH`` taken at import. The
connection layer reads its own module global, so to point the process at a different
database (tests do this) set ``database.connection.DB_PATH``, not ``database.DB_PATH``.
"""

from .connection import (  # noqa: F401
    DB_PATH,
    _ensure_conn,
    _fts5_available,
    _local,
    close_db,
    fts5_available,
    get_conn,
    run,
)

from .common import (  # noqa: F401
    _cutoff_date,
    _cutoff_datetime,
    _days_ago,
    _gini,
    _now,
    _today,
)

from .schema import (  # noqa: F401
    _SCHEMA_VERSION,
    _init_message_context_fts,
    _init_message_embeddings,
    _migrate_add_total_words,
    _migrate_timestamps,
    _run_migrations,
    init_db,
)

from .audit import (  # noqa: F401
    add_finding,
    finalize_audit_run,
    get_last_run,
    get_recent_findings,
    log_bulk_task,
    set_last_run,
    start_audit_run,
)

from .activity import (  # noqa: F401
    _MAX_ORPHAN_DURATION,
    bulk_log_member_events,
    bulk_log_message_events,
    bulk_log_reactions_received,
    clear_recent_events,
    close_orphaned_voice_sessions,
    decrement_reaction,
    end_voice_session,
    increment_reaction,
    log_member_event,
    log_message_event,
    prune_old_events,
    rollup_channel_activity,
    rollup_user_activity,
    save_member_snapshot,
    start_voice_session,
)

from .factcheck import (  # noqa: F401
    bulk_log_context_messages,
    count_embeddings,
    count_message_context,
    get_context_messages_by_ids,
    get_pending_context_rows,
    get_recent_context,
    get_relevant_history,
    get_two_tier_context,
    load_all_embeddings,
    log_context_message,
    prune_message_context,
    upsert_embeddings,
)

from .stats_read import (  # noqa: F401
    get_activity_diversity,
    get_channel_density,
    get_channel_growth,
    get_channel_growth_trends,
    get_channel_hourly_heatmap,
    get_channel_stats,
    get_channel_user_concentration,
    get_channel_weekday_split,
    get_channel_word_stats,
    get_churn_metrics,
    get_daily_activity,
    get_dau_wau_mau,
    get_hourly_weekday_weekend,
    get_join_day_distribution,
    get_leaderboard,
    get_member_events_summary,
    get_member_growth,
    get_message_velocity,
    get_peak_hours,
    get_per_channel_peak_hours,
    get_server_stats_summary,
    get_server_word_stats,
    get_top_channels,
    get_top_users,
    get_user_active_hours,
    get_user_dormancy,
    get_user_engagement_ratios,
    get_user_rank,
    get_user_stats,
    get_user_streaks,
    get_user_voice_session_count,
    get_user_weekday_split,
    get_user_word_stats,
    get_voice_channel_stats,
    get_voice_day_of_week,
    get_voice_leaderboard,
    get_voice_peak_hours,
    get_voice_session_distribution,
    get_weekday_weekend_split,
)
