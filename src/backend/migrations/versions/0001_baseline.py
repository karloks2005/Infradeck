"""The schema as it was when migrations were introduced.

A new database gets every table here. A database from before Alembic (created by ``create_all`` and the hand-written
statements that used to run at start) keeps its tables: only the tables it lacks are created, and the old statements
below bring the rest up to this schema. Both end at the same schema, which tests/history/test_migrations.py checks.
"""
from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

_existing: set[str] = set()


def _table(name, *elements, **kwargs) -> None:
    if name not in _existing:
        op.create_table(name, *elements, **kwargs)


def _index(name, table, columns, **kwargs) -> None:
    if table not in _existing:
        op.create_index(name, table, columns, **kwargs)


def upgrade() -> None:
    bind = op.get_bind()
    _existing.clear()
    _existing.update(sa.inspect(bind).get_table_names())
    _create_tables()
    if _existing:
        _bring_up_to_date(bind)


def _create_tables() -> None:
    _table('app_settings',
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('value', sa.Text(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    _table('audit_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('level', sa.String(length=50), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('source', sa.String(length=100), nullable=False),
    sa.Column('task_title', sa.String(length=255), nullable=True),
    sa.Column('steps', sa.Text(), nullable=True),
    sa.Column('conversation_id', sa.String(length=255), nullable=True),
    sa.Column('event_type', sa.String(length=50), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    _index(op.f('ix_audit_logs_conversation_id'), 'audit_logs', ['conversation_id'], unique=False)
    _index(op.f('ix_audit_logs_id'), 'audit_logs', ['id'], unique=False)
    _index(op.f('ix_audit_logs_user_id'), 'audit_logs', ['user_id'], unique=False)
    _table('chat_messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('conversation_id', sa.String(length=64), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    _index(op.f('ix_chat_messages_conversation_id'), 'chat_messages', ['conversation_id'], unique=False)
    _index(op.f('ix_chat_messages_id'), 'chat_messages', ['id'], unique=False)
    _table('checks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('type', sa.String(length=16), nullable=False),
    sa.Column('target', sa.String(length=2048), nullable=False),
    sa.Column('options', sa.Text(), nullable=False),
    sa.Column('tags', sa.String(length=255), nullable=False),
    sa.Column('server_ids', sa.Text(), nullable=False),
    sa.Column('rule', sa.String(length=8), nullable=False),
    sa.Column('interval_seconds', sa.Integer(), nullable=False),
    sa.Column('retries', sa.Integer(), nullable=False),
    sa.Column('retry_interval_seconds', sa.Integer(), nullable=False),
    sa.Column('timeout_seconds', sa.Integer(), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('paused', sa.Boolean(), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('state_since', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_result_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('rolled_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    _table('conversations',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    _index(op.f('ix_conversations_user_id'), 'conversations', ['user_id'], unique=False)
    _table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('role', sa.String(length=50), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('totp_secret_enc', sa.Text(), nullable=True),
    sa.Column('totp_enabled', sa.Boolean(), nullable=False),
    sa.Column('totp_last_step', sa.Integer(), nullable=True),
    sa.Column('password_changed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('username', sa.String(length=64), nullable=True),
    sa.Column('avatar_color', sa.String(length=16), nullable=True),
    sa.Column('preferences', sa.Text(), nullable=True),
    sa.Column('agent_chat_enabled', sa.Boolean(), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    _index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    _index(op.f('ix_users_id'), 'users', ['id'], unique=False)
    _table('agent_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('model', sa.String(length=100), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('cost_usd', sa.Float(), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('conversation_id', sa.String(length=255), nullable=True),
    sa.Column('incident_id', sa.Integer(), nullable=True),
    sa.Column('error', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    _index('ix_agent_runs_created', 'agent_runs', ['created_at'], unique=False)
    _index(op.f('ix_agent_runs_user_id'), 'agent_runs', ['user_id'], unique=False)
    _table('api_tokens',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('scope', sa.String(length=16), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('hint', sa.String(length=8), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    _index(op.f('ix_api_tokens_id'), 'api_tokens', ['id'], unique=False)
    _index(op.f('ix_api_tokens_user_id'), 'api_tokens', ['user_id'], unique=False)
    _table('check_results',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('check_id', sa.Integer(), nullable=False),
    sa.Column('server_id', sa.Integer(), nullable=False),
    sa.Column('time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ok', sa.Boolean(), nullable=False),
    sa.Column('response_ms', sa.Integer(), nullable=True),
    sa.Column('code', sa.Integer(), nullable=True),
    sa.Column('detail', sa.String(length=200), nullable=True),
    sa.Column('cert_days', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['check_id'], ['checks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    _index('ix_check_results_check_time', 'check_results', ['check_id', 'time'], unique=False)
    _table('check_rollups',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('check_id', sa.Integer(), nullable=False),
    sa.Column('hour', sa.DateTime(timezone=True), nullable=False),
    sa.Column('slots', sa.Integer(), nullable=False),
    sa.Column('down_slots', sa.Integer(), nullable=False),
    sa.Column('avg_ms', sa.Float(), nullable=True),
    sa.Column('max_ms', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['check_id'], ['checks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('check_id', 'hour')
    )
    _index(op.f('ix_check_rollups_check_id'), 'check_rollups', ['check_id'], unique=False)
    _table('enrollment_keys',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=False),
    sa.Column('key_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key_hash'),
    sa.UniqueConstraint('owner_id')
    )
    _index(op.f('ix_enrollment_keys_id'), 'enrollment_keys', ['id'], unique=False)
    _table('invites',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('role', sa.String(length=50), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    _index(op.f('ix_invites_id'), 'invites', ['id'], unique=False)
    _table('join_tokens',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    _index(op.f('ix_join_tokens_id'), 'join_tokens', ['id'], unique=False)
    _table('notification_channels',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('target_enc', sa.Text(), nullable=False),
    sa.Column('display', sa.String(length=120), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_status', sa.String(length=64), nullable=True),
    sa.Column('last_ok', sa.Boolean(), nullable=True),
    sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    _index(op.f('ix_notification_channels_id'), 'notification_channels', ['id'], unique=False)
    _index(op.f('ix_notification_channels_user_id'), 'notification_channels', ['user_id'], unique=False)
    _table('recovery_codes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code_hash')
    )
    _index(op.f('ix_recovery_codes_id'), 'recovery_codes', ['id'], unique=False)
    _index(op.f('ix_recovery_codes_user_id'), 'recovery_codes', ['user_id'], unique=False)
    _table('servers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=False),
    sa.Column('hostname', sa.String(length=255), nullable=False),
    sa.Column('ip_address', sa.String(length=64), nullable=True),
    sa.Column('os_info', sa.String(length=255), nullable=True),
    sa.Column('cpu_cores', sa.Integer(), nullable=True),
    sa.Column('total_ram_gb', sa.Float(), nullable=True),
    sa.Column('check_interval_seconds', sa.Integer(), nullable=False),
    sa.Column('collector_token_hash', sa.String(length=64), nullable=True),
    sa.Column('machine_id', sa.String(length=64), nullable=True),
    sa.Column('is_dashboard_host', sa.Boolean(), nullable=False),
    sa.Column('system_info', sa.Text(), nullable=True),
    sa.Column('name_locked', sa.Boolean(), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('collector_token_hash')
    )
    _index(op.f('ix_servers_id'), 'servers', ['id'], unique=False)
    _index(op.f('ix_servers_machine_id'), 'servers', ['machine_id'], unique=False)
    _index(op.f('ix_servers_owner_id'), 'servers', ['owner_id'], unique=False)
    _table('container_metrics',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('server_id', sa.Integer(), nullable=False),
    sa.Column('container_id', sa.String(length=128), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('image', sa.String(length=255), nullable=True),
    sa.Column('status', sa.String(length=64), nullable=False),
    sa.Column('restart_count', sa.Integer(), nullable=False),
    sa.Column('cpu_pct', sa.Float(), nullable=True),
    sa.Column('memory_used_mb', sa.Float(), nullable=True),
    sa.Column('memory_pct', sa.Float(), nullable=True),
    sa.Column('memory_limit_mb', sa.Float(), nullable=True),
    sa.Column('net_rx_mb', sa.Float(), nullable=True),
    sa.Column('net_tx_mb', sa.Float(), nullable=True),
    sa.Column('block_read_mb', sa.Float(), nullable=True),
    sa.Column('block_write_mb', sa.Float(), nullable=True),
    sa.Column('pids', sa.Integer(), nullable=True),
    sa.Column('started_at', sa.String(length=64), nullable=True),
    sa.Column('ports', sa.String(length=512), nullable=True),
    sa.Column('project', sa.String(length=128), nullable=True),
    sa.Column('swap_mb', sa.Float(), nullable=True),
    sa.Column('volumes', sa.String(length=512), nullable=True),
    sa.Column('healthcheck_status', sa.String(length=64), nullable=True),
    sa.Column('exit_code', sa.Integer(), nullable=True),
    sa.Column('oom_killed', sa.Boolean(), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('image_id', sa.String(length=80), nullable=True),
    sa.Column('time', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['server_id'], ['servers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    _index('ix_container_metrics_server_container_time', 'container_metrics', ['server_id', 'container_id', 'time'], unique=False)
    _index(op.f('ix_container_metrics_time'), 'container_metrics', ['time'], unique=False)
    _table('container_rollups',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('server_id', sa.Integer(), nullable=False),
    sa.Column('container_id', sa.String(length=128), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('tier', sa.String(length=8), nullable=False),
    sa.Column('bucket_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('samples', sa.Integer(), nullable=False),
    sa.Column('down_samples', sa.Integer(), nullable=False),
    sa.Column('avg_cpu', sa.Float(), nullable=True),
    sa.Column('n_cpu', sa.Integer(), nullable=False),
    sa.Column('max_cpu', sa.Float(), nullable=True),
    sa.Column('avg_memory_mb', sa.Float(), nullable=True),
    sa.Column('n_memory_mb', sa.Integer(), nullable=False),
    sa.Column('max_memory_mb', sa.Float(), nullable=True),
    sa.Column('avg_memory_pct', sa.Float(), nullable=True),
    sa.Column('n_memory_pct', sa.Integer(), nullable=False),
    sa.Column('max_memory_pct', sa.Float(), nullable=True),
    sa.Column('max_memory_limit_mb', sa.Float(), nullable=True),
    sa.Column('avg_swap_mb', sa.Float(), nullable=True),
    sa.Column('n_swap_mb', sa.Integer(), nullable=False),
    sa.Column('max_swap_mb', sa.Float(), nullable=True),
    sa.Column('max_restarts', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['server_id'], ['servers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('server_id', 'container_id', 'tier', 'bucket_start')
    )
    _index('ix_container_rollups_tier_bucket', 'container_rollups', ['tier', 'bucket_start'], unique=False)
    _table('firewall_states',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('server_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=16), nullable=False),
    sa.Column('sha', sa.String(length=16), nullable=False),
    sa.Column('rules', sa.Integer(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['server_id'], ['servers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('server_id', 'name')
    )
    _index(op.f('ix_firewall_states_id'), 'firewall_states', ['id'], unique=False)
    _index(op.f('ix_firewall_states_server_id'), 'firewall_states', ['server_id'], unique=False)
    _table('host_metrics',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('server_id', sa.Integer(), nullable=False),
    sa.Column('time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('cpu_utilization_pct', sa.Float(), nullable=True),
    sa.Column('load_1m', sa.Float(), nullable=True),
    sa.Column('load_5m', sa.Float(), nullable=True),
    sa.Column('load_15m', sa.Float(), nullable=True),
    sa.Column('ram_used_gb', sa.Float(), nullable=True),
    sa.Column('ram_available_gb', sa.Float(), nullable=True),
    sa.Column('ram_pct', sa.Float(), nullable=True),
    sa.Column('swap_pct', sa.Float(), nullable=True),
    sa.Column('disk_usage_pct', sa.Float(), nullable=True),
    sa.Column('disk_inodes_pct', sa.Float(), nullable=True),
    sa.Column('net_bytes_sent_mb', sa.Float(), nullable=True),
    sa.Column('net_bytes_recv_mb', sa.Float(), nullable=True),
    sa.Column('system_uptime_seconds', sa.Float(), nullable=True),
    sa.Column('boot_id', sa.String(length=64), nullable=True),
    sa.Column('oom_kill_total', sa.Integer(), nullable=True),
    sa.Column('kernel_tainted', sa.Integer(), nullable=True),
    sa.Column('ntp_synced', sa.Boolean(), nullable=True),
    sa.Column('reboot_required', sa.Boolean(), nullable=True),
    sa.Column('last_shutdown', sa.String(length=16), nullable=True),
    sa.Column('docker_status', sa.String(length=16), nullable=True),
    sa.Column('docker_reason', sa.String(length=16), nullable=True),
    sa.Column('docker_latency_ms', sa.Integer(), nullable=True),
    sa.Column('internet_status', sa.String(length=16), nullable=True),
    sa.Column('internet_latency_ms', sa.Integer(), nullable=True),
    sa.Column('collect_duration_ms', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['server_id'], ['servers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    _index('ix_host_metrics_server_time', 'host_metrics', ['server_id', 'time'], unique=False)
    _index(op.f('ix_host_metrics_time'), 'host_metrics', ['time'], unique=False)
    _table('host_rollups',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('server_id', sa.Integer(), nullable=False),
    sa.Column('tier', sa.String(length=8), nullable=False),
    sa.Column('bucket_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('samples', sa.Integer(), nullable=False),
    sa.Column('avg_cpu', sa.Float(), nullable=True),
    sa.Column('n_cpu', sa.Integer(), nullable=False),
    sa.Column('max_cpu', sa.Float(), nullable=True),
    sa.Column('avg_load', sa.Float(), nullable=True),
    sa.Column('n_load', sa.Integer(), nullable=False),
    sa.Column('max_load', sa.Float(), nullable=True),
    sa.Column('avg_ram', sa.Float(), nullable=True),
    sa.Column('n_ram', sa.Integer(), nullable=False),
    sa.Column('max_ram', sa.Float(), nullable=True),
    sa.Column('avg_swap', sa.Float(), nullable=True),
    sa.Column('n_swap', sa.Integer(), nullable=False),
    sa.Column('max_swap', sa.Float(), nullable=True),
    sa.Column('avg_disk', sa.Float(), nullable=True),
    sa.Column('n_disk', sa.Integer(), nullable=False),
    sa.Column('max_disk', sa.Float(), nullable=True),
    sa.Column('avg_inodes', sa.Float(), nullable=True),
    sa.Column('n_inodes', sa.Integer(), nullable=False),
    sa.Column('max_inodes', sa.Float(), nullable=True),
    sa.Column('avg_docker_ms', sa.Float(), nullable=True),
    sa.Column('n_docker_ms', sa.Integer(), nullable=False),
    sa.Column('max_docker_ms', sa.Float(), nullable=True),
    sa.Column('avg_collect_ms', sa.Float(), nullable=True),
    sa.Column('n_collect_ms', sa.Integer(), nullable=False),
    sa.Column('max_collect_ms', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['server_id'], ['servers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('server_id', 'tier', 'bucket_start')
    )
    _index('ix_host_rollups_tier_bucket', 'host_rollups', ['tier', 'bucket_start'], unique=False)
    _table('incident_alerts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('server_id', sa.Integer(), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('rule_key', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolution', sa.String(length=255), nullable=True),
    sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('acknowledged_by', sa.Integer(), nullable=True),
    sa.Column('muted_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolve_notified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reopen_after', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['server_id'], ['servers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    _index(op.f('ix_incident_alerts_created_at'), 'incident_alerts', ['created_at'], unique=False)
    _index(op.f('ix_incident_alerts_id'), 'incident_alerts', ['id'], unique=False)
    _index(op.f('ix_incident_alerts_rule_key'), 'incident_alerts', ['rule_key'], unique=False)
    _index(op.f('ix_incident_alerts_server_id'), 'incident_alerts', ['server_id'], unique=False)
    _table('listener_states',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('server_id', sa.Integer(), nullable=False),
    sa.Column('proto', sa.String(length=8), nullable=False),
    sa.Column('port', sa.Integer(), nullable=False),
    sa.Column('scope', sa.String(length=16), nullable=False),
    sa.Column('first_seen', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['server_id'], ['servers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('server_id', 'proto', 'port', 'scope')
    )
    _index(op.f('ix_listener_states_id'), 'listener_states', ['id'], unique=False)
    _index(op.f('ix_listener_states_server_id'), 'listener_states', ['server_id'], unique=False)
    _table('mount_metrics',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('server_id', sa.Integer(), nullable=False),
    sa.Column('time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('mount', sa.String(length=255), nullable=False),
    sa.Column('fs_type', sa.String(length=64), nullable=True),
    sa.Column('total_gb', sa.Float(), nullable=True),
    sa.Column('used_gb', sa.Float(), nullable=True),
    sa.Column('used_pct', sa.Float(), nullable=True),
    sa.Column('inodes_pct', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['server_id'], ['servers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    _index('ix_mount_metrics_server_time', 'mount_metrics', ['server_id', 'time'], unique=False)
    _index(op.f('ix_mount_metrics_time'), 'mount_metrics', ['time'], unique=False)
    _table('mount_rollups',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('server_id', sa.Integer(), nullable=False),
    sa.Column('mount', sa.String(length=255), nullable=False),
    sa.Column('tier', sa.String(length=8), nullable=False),
    sa.Column('bucket_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('samples', sa.Integer(), nullable=False),
    sa.Column('avg_used_pct', sa.Float(), nullable=True),
    sa.Column('n_used_pct', sa.Integer(), nullable=False),
    sa.Column('max_used_pct', sa.Float(), nullable=True),
    sa.Column('avg_used_gb', sa.Float(), nullable=True),
    sa.Column('n_used_gb', sa.Integer(), nullable=False),
    sa.Column('max_total_gb', sa.Float(), nullable=True),
    sa.Column('avg_inodes_pct', sa.Float(), nullable=True),
    sa.Column('n_inodes_pct', sa.Integer(), nullable=False),
    sa.Column('max_inodes_pct', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['server_id'], ['servers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('server_id', 'mount', 'tier', 'bucket_start')
    )
    _index('ix_mount_rollups_tier_bucket', 'mount_rollups', ['tier', 'bucket_start'], unique=False)
    _table('pending_collectors',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('poll_secret_hash', sa.String(length=64), nullable=False),
    sa.Column('pairing_code', sa.String(length=16), nullable=False),
    sa.Column('hostname', sa.String(length=255), nullable=False),
    sa.Column('machine_id', sa.String(length=64), nullable=True),
    sa.Column('ip_address', sa.String(length=64), nullable=True),
    sa.Column('os_info', sa.String(length=255), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('details', sa.Text(), nullable=True),
    sa.Column('approved_server_id', sa.Integer(), nullable=True),
    sa.Column('join_token_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_poll_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['approved_server_id'], ['servers.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['join_token_id'], ['join_tokens.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('poll_secret_hash')
    )
    _index(op.f('ix_pending_collectors_id'), 'pending_collectors', ['id'], unique=False)
    _index(op.f('ix_pending_collectors_machine_id'), 'pending_collectors', ['machine_id'], unique=False)
    _table('system_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('server_id', sa.Integer(), nullable=False),
    sa.Column('time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('event_key', sa.String(length=128), nullable=True),
    sa.Column('detail', sa.String(length=512), nullable=False),
    sa.ForeignKeyConstraint(['server_id'], ['servers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    _index(op.f('ix_system_events_event_key'), 'system_events', ['event_key'], unique=False)
    _index('ix_system_events_server_time', 'system_events', ['server_id', 'time'], unique=False)
    _index(op.f('ix_system_events_time'), 'system_events', ['time'], unique=False)
    _table('agent_reports',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('incident_id', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('trigger', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('model', sa.String(length=100), nullable=False),
    sa.Column('confidence', sa.String(length=16), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('body', sa.Text(), nullable=True),
    sa.Column('evidence', sa.Text(), nullable=True),
    sa.Column('error', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['incident_id'], ['incident_alerts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('incident_id', 'version')
    )
    _index(op.f('ix_agent_reports_incident_id'), 'agent_reports', ['incident_id'], unique=False)


def _bring_up_to_date(bind) -> None:
    """The statements that ran at every start before migrations, for databases created back then. Each is safe to
    repeat and does nothing on a database that already has the change."""
    if bind.dialect.name == "postgresql":
        execute = lambda statement, **values: bind.execute(sa.text(statement), values)  # noqa: E731
        for column, column_type in (("task_title", "VARCHAR(255)"), ("steps", "TEXT"), ("conversation_id", "VARCHAR(255)"), ("event_type", "VARCHAR(50)")):
            execute(f"ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS {column} {column_type}")
        for column, column_type in (
            ("memory_limit_mb", "DOUBLE PRECISION"), ("net_rx_mb", "DOUBLE PRECISION"), ("net_tx_mb", "DOUBLE PRECISION"),
            ("block_read_mb", "DOUBLE PRECISION"), ("block_write_mb", "DOUBLE PRECISION"), ("pids", "INTEGER"),
            ("started_at", "VARCHAR(64)"), ("ports", "VARCHAR(512)"),
            ("exit_code", "INTEGER"), ("oom_killed", "BOOLEAN"), ("finished_at", "TIMESTAMP WITH TIME ZONE"), ("image_id", "VARCHAR(80)"),
            ("project", "VARCHAR(128)"), ("volumes", "VARCHAR(512)"), ("swap_mb", "DOUBLE PRECISION"),
        ):
            execute(f"ALTER TABLE container_metrics ADD COLUMN IF NOT EXISTS {column} {column_type}")
        for column, column_type in (
            ("boot_id", "VARCHAR(64)"), ("oom_kill_total", "INTEGER"), ("kernel_tainted", "INTEGER"),
            ("ntp_synced", "BOOLEAN"), ("reboot_required", "BOOLEAN"), ("last_shutdown", "VARCHAR(16)"),
            ("docker_status", "VARCHAR(16)"), ("docker_reason", "VARCHAR(16)"), ("docker_latency_ms", "INTEGER"),
            ("internet_status", "VARCHAR(16)"), ("internet_latency_ms", "INTEGER"), ("collect_duration_ms", "INTEGER"),
        ):
            execute(f"ALTER TABLE host_metrics ADD COLUMN IF NOT EXISTS {column} {column_type}")
        execute("ALTER TABLE system_events ADD COLUMN IF NOT EXISTS event_key VARCHAR(128)")
        execute("ALTER TABLE pending_collectors ADD COLUMN IF NOT EXISTS join_token_id INTEGER REFERENCES join_tokens(id) ON DELETE SET NULL")
        execute("ALTER TABLE pending_collectors ADD COLUMN IF NOT EXISTS details TEXT")
        for column, column_type in (("avg_swap_mb", "DOUBLE PRECISION"), ("n_swap_mb", "INTEGER NOT NULL DEFAULT 0"), ("max_swap_mb", "DOUBLE PRECISION")):
            execute(f"ALTER TABLE container_rollups ADD COLUMN IF NOT EXISTS {column} {column_type}")
        execute("CREATE INDEX IF NOT EXISTS ix_system_events_event_key ON system_events (event_key)")
        # Composite indexes that match how the metrics tables are read, then the single-column ones they made
        # redundant. Creating an index briefly blocks writes to that table.
        for table, name, columns in (
            ("host_metrics", "ix_host_metrics_server_time", "server_id, time"),
            ("mount_metrics", "ix_mount_metrics_server_time", "server_id, time"),
            ("system_events", "ix_system_events_server_time", "server_id, time"),
            ("container_metrics", "ix_container_metrics_server_container_time", "server_id, container_id, time"),
        ):
            execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({columns})")
        for redundant in (
            "ix_host_metrics_id", "ix_host_metrics_server_id", "ix_mount_metrics_id", "ix_mount_metrics_server_id",
            "ix_system_events_id", "ix_system_events_server_id",
            "ix_container_metrics_id", "ix_container_metrics_server_id", "ix_container_metrics_container_id",
        ):
            execute(f"DROP INDEX IF EXISTS {redundant}")
        for column, column_type in (
            ("totp_secret_enc", "TEXT"), ("totp_enabled", "BOOLEAN NOT NULL DEFAULT FALSE"), ("totp_last_step", "INTEGER"), ("password_changed_at", "TIMESTAMP WITH TIME ZONE"),
            ("username", "VARCHAR(64)"), ("avatar_color", "VARCHAR(16)"), ("preferences", "TEXT"), ("last_seen_at", "TIMESTAMP WITH TIME ZONE"), ("agent_chat_enabled", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ):
            execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {column} {column_type}")
        for column, column_type in (
            ("rule_key", "VARCHAR(255)"), ("resolved_at", "TIMESTAMP WITH TIME ZONE"), ("resolution", "VARCHAR(255)"), ("acknowledged_at", "TIMESTAMP WITH TIME ZONE"),
            ("acknowledged_by", "INTEGER"), ("muted_until", "TIMESTAMP WITH TIME ZONE"), ("notified_at", "TIMESTAMP WITH TIME ZONE"),
            ("resolve_notified_at", "TIMESTAMP WITH TIME ZONE"), ("reopen_after", "TIMESTAMP WITH TIME ZONE"),
        ):
            execute(f"ALTER TABLE incident_alerts ADD COLUMN IF NOT EXISTS {column} {column_type}")
        execute("CREATE INDEX IF NOT EXISTS ix_incident_alerts_rule_key ON incident_alerts (rule_key)")
        # The users table used to come from docker/postgres/init with its own index names; give it the models'
        # ones. The unique index exists before the old unique constraint goes, so emails stay unique throughout.
        execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)")
        execute("CREATE INDEX IF NOT EXISTS ix_users_id ON users (id)")
        execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_email_key")
        execute("DROP INDEX IF EXISTS idx_users_email")
        execute("DROP INDEX IF EXISTS idx_users_role")
        # Alerts from before the alert engine: offline alerts keep going under their rule, the rest were
        # duplicated on every check-in with no way to resolve, so close them; the engine re-raises live ones.
        execute("UPDATE incident_alerts SET rule_key = 'server_offline' WHERE rule_key IS NULL AND description = :description",
                description="The collector has stopped reporting. Check that the server is up and can reach the backend.")
        execute("UPDATE incident_alerts SET status = 'resolved', resolved_at = NOW() WHERE rule_key IS NULL AND status = 'active'")
        # Disk and inode alerts became per mount; the old keys were for the root filesystem.
        execute("UPDATE incident_alerts SET rule_key = rule_key || ':/' WHERE rule_key IN ('host:disk', 'host:inodes')")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("servers")}
    for column, column_type in (("collector_token_hash", "VARCHAR(64)"), ("machine_id", "VARCHAR(64)"), ("is_dashboard_host", "BOOLEAN NOT NULL DEFAULT FALSE"),
                                ("system_info", "TEXT"), ("name_locked", "BOOLEAN NOT NULL DEFAULT FALSE")):
        if column not in columns:
            bind.execute(sa.text(f"ALTER TABLE servers ADD COLUMN {column} {column_type}"))


def downgrade() -> None:
    op.drop_index(op.f('ix_agent_reports_incident_id'), table_name='agent_reports')
    op.drop_table('agent_reports')
    op.drop_index(op.f('ix_system_events_time'), table_name='system_events')
    op.drop_index('ix_system_events_server_time', table_name='system_events')
    op.drop_index(op.f('ix_system_events_event_key'), table_name='system_events')
    op.drop_table('system_events')
    op.drop_index(op.f('ix_pending_collectors_machine_id'), table_name='pending_collectors')
    op.drop_index(op.f('ix_pending_collectors_id'), table_name='pending_collectors')
    op.drop_table('pending_collectors')
    op.drop_index('ix_mount_rollups_tier_bucket', table_name='mount_rollups')
    op.drop_table('mount_rollups')
    op.drop_index(op.f('ix_mount_metrics_time'), table_name='mount_metrics')
    op.drop_index('ix_mount_metrics_server_time', table_name='mount_metrics')
    op.drop_table('mount_metrics')
    op.drop_index(op.f('ix_listener_states_server_id'), table_name='listener_states')
    op.drop_index(op.f('ix_listener_states_id'), table_name='listener_states')
    op.drop_table('listener_states')
    op.drop_index(op.f('ix_incident_alerts_server_id'), table_name='incident_alerts')
    op.drop_index(op.f('ix_incident_alerts_rule_key'), table_name='incident_alerts')
    op.drop_index(op.f('ix_incident_alerts_id'), table_name='incident_alerts')
    op.drop_index(op.f('ix_incident_alerts_created_at'), table_name='incident_alerts')
    op.drop_table('incident_alerts')
    op.drop_index('ix_host_rollups_tier_bucket', table_name='host_rollups')
    op.drop_table('host_rollups')
    op.drop_index(op.f('ix_host_metrics_time'), table_name='host_metrics')
    op.drop_index('ix_host_metrics_server_time', table_name='host_metrics')
    op.drop_table('host_metrics')
    op.drop_index(op.f('ix_firewall_states_server_id'), table_name='firewall_states')
    op.drop_index(op.f('ix_firewall_states_id'), table_name='firewall_states')
    op.drop_table('firewall_states')
    op.drop_index('ix_container_rollups_tier_bucket', table_name='container_rollups')
    op.drop_table('container_rollups')
    op.drop_index(op.f('ix_container_metrics_time'), table_name='container_metrics')
    op.drop_index('ix_container_metrics_server_container_time', table_name='container_metrics')
    op.drop_table('container_metrics')
    op.drop_index(op.f('ix_servers_owner_id'), table_name='servers')
    op.drop_index(op.f('ix_servers_machine_id'), table_name='servers')
    op.drop_index(op.f('ix_servers_id'), table_name='servers')
    op.drop_table('servers')
    op.drop_index(op.f('ix_recovery_codes_user_id'), table_name='recovery_codes')
    op.drop_index(op.f('ix_recovery_codes_id'), table_name='recovery_codes')
    op.drop_table('recovery_codes')
    op.drop_index(op.f('ix_notification_channels_user_id'), table_name='notification_channels')
    op.drop_index(op.f('ix_notification_channels_id'), table_name='notification_channels')
    op.drop_table('notification_channels')
    op.drop_index(op.f('ix_join_tokens_id'), table_name='join_tokens')
    op.drop_table('join_tokens')
    op.drop_index(op.f('ix_invites_id'), table_name='invites')
    op.drop_table('invites')
    op.drop_index(op.f('ix_enrollment_keys_id'), table_name='enrollment_keys')
    op.drop_table('enrollment_keys')
    op.drop_index(op.f('ix_check_rollups_check_id'), table_name='check_rollups')
    op.drop_table('check_rollups')
    op.drop_index('ix_check_results_check_time', table_name='check_results')
    op.drop_table('check_results')
    op.drop_index(op.f('ix_api_tokens_user_id'), table_name='api_tokens')
    op.drop_index(op.f('ix_api_tokens_id'), table_name='api_tokens')
    op.drop_table('api_tokens')
    op.drop_index(op.f('ix_agent_runs_user_id'), table_name='agent_runs')
    op.drop_index('ix_agent_runs_created', table_name='agent_runs')
    op.drop_table('agent_runs')
    op.drop_index(op.f('ix_users_id'), table_name='users')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
    op.drop_index(op.f('ix_conversations_user_id'), table_name='conversations')
    op.drop_table('conversations')
    op.drop_table('checks')
    op.drop_index(op.f('ix_chat_messages_id'), table_name='chat_messages')
    op.drop_index(op.f('ix_chat_messages_conversation_id'), table_name='chat_messages')
    op.drop_table('chat_messages')
    op.drop_index(op.f('ix_audit_logs_user_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_conversation_id'), table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_table('app_settings')
