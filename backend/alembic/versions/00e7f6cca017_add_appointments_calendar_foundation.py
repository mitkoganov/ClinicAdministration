"""add appointments calendar foundation

Revision ID: 00e7f6cca017
Revises: 65f7891a7fc7
Create Date: 2026-07-24 09:29:11.345394

MED-005: rooms, service types, recurring provider schedules (+ breaks),
one-off calendar blocks, and appointments, plus a new `tenants.timezone`
column. Introduces this codebase's first PostgreSQL exclusion
constraints - `btree_gist` is enabled first because the GiST index type
the `Appointment` exclusion constraints use needs its `=` operator class
for `uuid`, which only `btree_gist` provides (a plain `gist` index alone
only understands range/geometric types out of the box).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "00e7f6cca017"
down_revision: str | None = "65f7891a7fc7"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Required by the two Appointment exclusion constraints below (GiST
    # equality support for uuid/enum-as-varchar columns comes from this
    # extension, not from a bare `gist` index). `IF NOT EXISTS` makes this
    # safe to re-run and safe on a database where another migration
    # already enabled it.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "appointment_service_types",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_duration_minutes", sa.Integer(), nullable=False),
        sa.Column("buffer_before_minutes", sa.Integer(), nullable=False),
        sa.Column("buffer_after_minutes", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "inactive", name="service_type_status", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "buffer_after_minutes >= 0 AND buffer_after_minutes <= 480",
            name="ck_appointment_service_types_buffer_after_range",
        ),
        sa.CheckConstraint(
            "buffer_before_minutes >= 0 AND buffer_before_minutes <= 480",
            name="ck_appointment_service_types_buffer_before_range",
        ),
        sa.CheckConstraint(
            "default_duration_minutes > 0 AND default_duration_minutes <= 1440",
            name="ck_appointment_service_types_duration_range",
        ),
        sa.CheckConstraint(
            "length(btrim(code)) > 0", name="ck_appointment_service_types_code_not_blank"
        ),
        sa.CheckConstraint(
            "length(btrim(name)) > 0", name="ck_appointment_service_types_name_not_blank"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_appointment_service_types_tenant_code"),
    )
    op.create_index(
        "ix_appointment_service_types_tenant_id",
        "appointment_service_types",
        ["tenant_id"],
        unique=False,
    )
    op.create_table(
        "clinic_rooms",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("active", "inactive", name="clinic_room_status", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(btrim(code)) > 0", name="ck_clinic_rooms_code_not_blank"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_clinic_rooms_name_not_blank"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_clinic_rooms_tenant_code"),
    )
    op.create_index("ix_clinic_rooms_tenant_id", "clinic_rooms", ["tenant_id"], unique=False)
    op.create_table(
        "appointments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("provider_user_id", sa.Uuid(), nullable=False),
        sa.Column("room_id", sa.Uuid(), nullable=True),
        sa.Column("service_type_id", sa.Uuid(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "scheduled",
                "confirmed",
                "cancelled",
                "completed",
                "no_show",
                name="appointment_status",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("patient_display_name", sa.String(length=200), nullable=False),
        sa.Column("patient_phone", sa.String(length=32), nullable=True),
        sa.Column("patient_email", sa.String(length=320), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("cancellation_reason", sa.String(length=300), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        postgresql.ExcludeConstraint(
            (sa.column("tenant_id"), "="),
            (sa.column("provider_user_id"), "="),
            (sa.text("tstzrange(starts_at, ends_at, '[)')"), "&&"),
            where=sa.text("status IN ('scheduled', 'confirmed')"),
            using="gist",
            name="ex_appointments_provider_overlap",
        ),
        postgresql.ExcludeConstraint(
            (sa.column("tenant_id"), "="),
            (sa.column("room_id"), "="),
            (sa.text("tstzrange(starts_at, ends_at, '[)')"), "&&"),
            where=sa.text("room_id IS NOT NULL AND status IN ('scheduled', 'confirmed')"),
            using="gist",
            name="ex_appointments_room_overlap",
        ),
        sa.CheckConstraint(
            "length(btrim(patient_display_name)) > 0",
            name="ck_appointments_patient_display_name_not_blank",
        ),
        sa.CheckConstraint(
            "length(cancellation_reason) <= 300", name="ck_appointments_cancellation_reason_length"
        ),
        sa.CheckConstraint("length(notes) <= 2000", name="ck_appointments_notes_length"),
        sa.CheckConstraint("starts_at < ends_at", name="ck_appointments_start_before_end"),
        sa.CheckConstraint("version >= 1", name="ck_appointments_version_positive"),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["clinic_rooms.id"],
        ),
        sa.ForeignKeyConstraint(
            ["service_type_id"],
            ["appointment_service_types.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_appointments_tenant_id", "appointments", ["tenant_id"], unique=False)
    op.create_index(
        "ix_appointments_tenant_provider_range",
        "appointments",
        ["tenant_id", "provider_user_id", "starts_at"],
        unique=False,
    )
    op.create_index(
        "ix_appointments_tenant_room_range",
        "appointments",
        ["tenant_id", "room_id", "starts_at"],
        unique=False,
    )
    op.create_index(
        "ix_appointments_tenant_status", "appointments", ["tenant_id", "status"], unique=False
    )
    op.create_table(
        "calendar_blocks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("provider_user_id", sa.Uuid(), nullable=True),
        sa.Column("room_id", sa.Uuid(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=False),
        sa.Column(
            "block_type",
            sa.Enum(
                "leave",
                "training",
                "maintenance",
                "room_closure",
                "personal",
                "other",
                name="calendar_block_type",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(btrim(reason)) > 0", name="ck_calendar_blocks_reason_not_blank"),
        sa.CheckConstraint(
            "provider_user_id IS NOT NULL OR room_id IS NOT NULL",
            name="ck_calendar_blocks_provider_or_room",
        ),
        sa.CheckConstraint("starts_at < ends_at", name="ck_calendar_blocks_start_before_end"),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["clinic_rooms.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_calendar_blocks_tenant_id", "calendar_blocks", ["tenant_id"], unique=False)
    op.create_index(
        "ix_calendar_blocks_tenant_provider_range",
        "calendar_blocks",
        ["tenant_id", "provider_user_id", "starts_at"],
        unique=False,
    )
    op.create_index(
        "ix_calendar_blocks_tenant_room_range",
        "calendar_blocks",
        ["tenant_id", "room_id", "starts_at"],
        unique=False,
    )
    op.create_table(
        "provider_schedules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("provider_user_id", sa.Uuid(), nullable=False),
        sa.Column("day_of_week", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_until", sa.Date(), nullable=True),
        sa.Column("room_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "active", "inactive", name="provider_schedule_status", native_enum=False, length=20
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "day_of_week >= 0 AND day_of_week <= 6", name="ck_provider_schedules_day_of_week_range"
        ),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until >= effective_from",
            name="ck_provider_schedules_effective_range",
        ),
        sa.CheckConstraint("start_time < end_time", name="ck_provider_schedules_start_before_end"),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["clinic_rooms.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_provider_schedules_tenant_id", "provider_schedules", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_provider_schedules_tenant_provider_day",
        "provider_schedules",
        ["tenant_id", "provider_user_id", "day_of_week"],
        unique=False,
    )
    op.create_table(
        "schedule_breaks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("schedule_id", sa.Uuid(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=True),
        sa.CheckConstraint("start_time < end_time", name="ck_schedule_breaks_start_before_end"),
        sa.ForeignKeyConstraint(["schedule_id"], ["provider_schedules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_schedule_breaks_schedule_id", "schedule_breaks", ["schedule_id"], unique=False
    )
    op.add_column(
        "tenants",
        sa.Column("timezone", sa.String(length=64), server_default="Europe/Sofia", nullable=False),
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column("tenants", "timezone")
    op.drop_index("ix_schedule_breaks_schedule_id", table_name="schedule_breaks")
    op.drop_table("schedule_breaks")
    op.drop_index("ix_provider_schedules_tenant_provider_day", table_name="provider_schedules")
    op.drop_index("ix_provider_schedules_tenant_id", table_name="provider_schedules")
    op.drop_table("provider_schedules")
    op.drop_index("ix_calendar_blocks_tenant_room_range", table_name="calendar_blocks")
    op.drop_index("ix_calendar_blocks_tenant_provider_range", table_name="calendar_blocks")
    op.drop_index("ix_calendar_blocks_tenant_id", table_name="calendar_blocks")
    op.drop_table("calendar_blocks")
    op.drop_index("ix_appointments_tenant_status", table_name="appointments")
    op.drop_index("ix_appointments_tenant_room_range", table_name="appointments")
    op.drop_index("ix_appointments_tenant_provider_range", table_name="appointments")
    op.drop_index("ix_appointments_tenant_id", table_name="appointments")
    op.drop_table("appointments")
    op.drop_index("ix_clinic_rooms_tenant_id", table_name="clinic_rooms")
    op.drop_table("clinic_rooms")
    op.drop_index("ix_appointment_service_types_tenant_id", table_name="appointment_service_types")
    op.drop_table("appointment_service_types")
    # ### end Alembic commands ###
    # Deliberately does NOT `DROP EXTENSION btree_gist` - a database-wide
    # extension another migration or another schema object could depend
    # on later; removing it here on a MED-005-only downgrade is out of
    # this migration's blast radius. Re-enabling it again on a future
    # re-upgrade is a no-op (`IF NOT EXISTS`).
