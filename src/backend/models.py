from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(50), default="user", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Two-factor sign-in: the TOTP secret is stored encrypted (see secrets_box); it only counts once totp_enabled is set.
    totp_secret_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # The newest time step already accepted, so an intercepted code can't be replayed.
    totp_last_step: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    password_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class RecoveryCode(Base):
    """A single-use two-factor recovery code, stored only as a hash."""

    __tablename__ = "recovery_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AppSetting(Base):
    """Admin-editable settings stored as key/value pairs."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    level: Mapped[str] = mapped_column(String(50), default="INFO", nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(100), default="system", nullable=False)
    task_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    steps: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    conversation_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    event_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="New chat")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    os_info: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    cpu_cores: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_ram_gb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    check_interval_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    collector_token_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)
    # Stable host identity reported by self-enrolling collectors, so a reinstall maps back to the same server.
    machine_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    # The machine running the dashboard itself, as reported by the collector in its compose stack.
    is_dashboard_host: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class PendingCollector(Base):
    """A collector that asked to join and is waiting for a user to Add or Dismiss it."""

    __tablename__ = "pending_collectors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    # Hash of the secret the collector polls with; only that collector can pick up its token.
    poll_secret_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    pairing_code: Mapped[str] = mapped_column(String(16), nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    machine_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    os_info: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)  # pending | dismissed | approved
    approved_server_id: Mapped[Optional[int]] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), nullable=True)
    # The join token this request presented, if any (shown as "verified" on the dashboard).
    join_token_id: Mapped[Optional[int]] = mapped_column(ForeignKey("join_tokens.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    last_poll_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class JoinToken(Base):
    """Minted by the Connect a server button and embedded in its command; proves who asked a collector to join."""

    __tablename__ = "join_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Set when the server it created is approved; a used token can't register anything else.
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class EnrollmentKey(Base):
    __tablename__ = "enrollment_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class HostMetric(Base):
    __tablename__ = "host_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True, nullable=False)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True, nullable=False)
    cpu_utilization_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    load_1m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    load_5m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    load_15m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ram_used_gb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ram_available_gb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ram_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    swap_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    disk_usage_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    disk_inodes_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    net_bytes_sent_mb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    net_bytes_recv_mb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    system_uptime_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # System state read from the kernel; None means the collector couldn't tell.
    boot_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    oom_kill_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    kernel_tainted: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ntp_synced: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    reboot_required: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    # How the previous boot ended (clean | unclean | unknown), from the optional system helper.
    last_shutdown: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # Whether the collector could reach the Docker API (ok | unreachable | not_configured), why not, and how long
    # listing the containers took. Lets "Docker is down" be told apart from "there are no containers".
    docker_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    docker_reason: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    docker_latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Whether the host can reach the internet (ok | degraded | dns_failed | down); None when the check is off.
    internet_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    internet_latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # How long the collector took to gather this check-in (milliseconds); None from collectors that don't say.
    collect_duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class SystemEvent(Base):
    """Something that happened on a host (reboot, OOM kill, kernel fault), found by comparing check-ins."""

    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True, nullable=False)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # reboot | oom_kill | kernel_fault | kernel_error
    # Identifies the occurrence so a snapshot that repeats it doesn't record it twice.
    event_key: Mapped[Optional[str]] = mapped_column(String(128), index=True, nullable=True)
    detail: Mapped[str] = mapped_column(String(512), nullable=False)


class MountMetric(Base):
    """Usage of one filesystem at one check-in; shares its time with that check-in's HostMetric."""

    __tablename__ = "mount_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True, nullable=False)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True, nullable=False)
    mount: Mapped[str] = mapped_column(String(255), nullable=False)
    fs_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    total_gb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    used_gb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    used_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    inodes_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class ListenerState(Base):
    """A port the host is listening on, as last seen. One row per listener, so comparing check-ins needs no history."""

    __tablename__ = "listener_states"
    __table_args__ = (UniqueConstraint("server_id", "proto", "port", "scope"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True, nullable=False)
    proto: Mapped[str] = mapped_column(String(8), nullable=False)  # tcp | udp
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)  # loopback | any | specific
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class FirewallState(Base):
    """Fingerprint of one firewall configuration file as last seen: a hash and a rule count, never the rules."""

    __tablename__ = "firewall_states"
    __table_args__ = (UniqueConstraint("server_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(16), nullable=False)
    sha: Mapped[str] = mapped_column(String(16), nullable=False)
    rules: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)  # only meaningful for ufw-conf
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class ContainerMetric(Base):
    __tablename__ = "container_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True, nullable=False)
    container_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    image: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    restart_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cpu_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    memory_used_mb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    memory_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    memory_limit_mb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    net_rx_mb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    net_tx_mb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    block_read_mb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    block_write_mb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pids: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    started_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    ports: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    healthcheck_status: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # How a stopped container ended, and the ID of the image it runs (to notice it changing).
    exit_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    oom_killed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    image_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True, nullable=False)


class IncidentAlert(Base):
    __tablename__ = "incident_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="WARNING", nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    # Identifies the condition behind the alert (e.g. "server_offline"), so a server has at most one active alert per rule.
    rule_key: Mapped[Optional[str]] = mapped_column(String(255), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True, nullable=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
