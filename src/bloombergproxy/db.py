"""Tabellen des Lizenz-Service (Spiegel von db/ddl.sql) und Engine-Fabrik."""

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Engine,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    create_engine,
)
from sqlalchemy.pool import StaticPool

metadata = MetaData()

instrument = Table(
    "instrument", metadata,
    Column("instrument_id", String, primary_key=True),
    Column("figi", String),
    Column("isin", String),
    Column("cusip", String),
    Column("sedol", String),
    Column("ticker", String),
    Column("name", String),
    Column("status", String, nullable=False),
    Column("source", String, nullable=False),
    Column("subscribed_since", Date),
    Column("unsubscribe_effective", Date),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    schema="app",
)

field_catalog = Table(
    "field_catalog", metadata,
    Column("mnemonic", String, primary_key=True),
    Column("description", String),
    Column("category", String, nullable=False),
    Column("data_type", String),
    schema="app",
)

field_set = Table(
    "field_set", metadata,
    Column("field_set_id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("description", String),
    schema="app",
)

field_set_field = Table(
    "field_set_field", metadata,
    Column("field_set_id", String, primary_key=True),
    Column("mnemonic", String, primary_key=True),
    schema="app",
)

instrument_field_set = Table(
    "instrument_field_set", metadata,
    Column("instrument_id", String, primary_key=True),
    Column("field_set_id", String, primary_key=True),
    Column("since", Date, nullable=False),
    schema="app",
)

category_entitlement = Table(
    "category_entitlement", metadata,
    Column("category", String, primary_key=True),
    Column("required_role", String),
    Column("redistributable", Boolean, nullable=False, default=True),
    Column("note", String),
    schema="app",
)

pricing_category = Table(
    "pricing_category", metadata,
    Column("category", String, primary_key=True),
    Column("valid_from", Date, primary_key=True),
    Column("valid_to", Date),
    Column("price_per_instrument", Numeric(12, 2), nullable=False),
    Column("currency", String, nullable=False),
    schema="app",
)

order = Table(
    "order", metadata,
    Column("order_id", String, primary_key=True),
    Column("order_type", String, nullable=False),
    Column("status", String, nullable=False),
    Column("reason", String),
    Column("field_sets", String),
    Column("estimated_cost_chf", Numeric(12, 2)),
    Column("estimate_json", String),
    Column("history_fields", String),
    Column("history_from", Date),
    Column("history_to", Date),
    Column("requires_approval", Boolean, nullable=False),
    Column("source", String, nullable=False, default="MANUAL"),
    Column("requested_by", String, nullable=False),
    Column("requested_at", DateTime, nullable=False),
    Column("decided_by", String),
    Column("decided_at", DateTime),
    Column("decision_comment", String),
    Column("effective_date", Date),
    Column("bbg_request_id", String),
    Column("idempotency_key", String),
    schema="app",
)

order_item = Table(
    "order_item", metadata,
    Column("order_id", String, primary_key=True),
    Column("line_no", Integer, primary_key=True),
    Column("identifier_type", String, nullable=False),
    Column("identifier_value", String, nullable=False),
    Column("instrument_id", String),
    Column("is_duplicate", Boolean, nullable=False, default=False),
    schema="app",
)

audit_log = Table(
    "audit_log", metadata,
    Column("at", DateTime, nullable=False),
    Column("user_name", String, nullable=False),
    Column("action", String, nullable=False),
    Column("entity", String, nullable=False),
    Column("entity_id", String, nullable=False),
    Column("details", String),
    schema="app",
)

job = Table(
    "job", metadata,
    Column("job_id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("status", String, nullable=False),
    Column("started_at", DateTime),
    Column("finished_at", DateTime),
    Column("summary", String),
    schema="app",
)

notification = Table(
    "notification", metadata,
    Column("notification_id", String, primary_key=True),
    Column("created_at", DateTime, nullable=False),
    Column("event", String, nullable=False),
    Column("title", String, nullable=False),
    Column("text", String, nullable=False),
    Column("details", String),
    Column("status", String, nullable=False),
    Column("error", String),
    Column("sent_at", DateTime),
    schema="app",
)

vendor_file_raw = Table(
    "vendor_file_raw", metadata,
    Column("vendor", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("file_name", String, nullable=False),
    Column("received_at", DateTime, nullable=False),
    Column("line_no", Integer, nullable=False),
    Column("payload", String, nullable=False),
    schema="bronze",
)

bbg_response_raw = Table(
    "bbg_response_raw", metadata,
    Column("request_id", String, nullable=False),
    Column("file_name", String, nullable=False),
    Column("received_at", DateTime, nullable=False),
    Column("line_no", Integer, nullable=False),
    Column("payload", String, nullable=False),
    schema="bronze",
)

billing_instrument_month = Table(
    "billing_instrument_month", metadata,
    Column("billing_month", String, primary_key=True),
    Column("instrument_id", String, primary_key=True),
    Column("category", String, primary_key=True),
    Column("first_requested_at", DateTime, nullable=False),
    schema="gold",
)

position = Table(
    "position", metadata,
    Column("as_of_date", Date, nullable=False),
    Column("portfolio", String, nullable=False),
    Column("isin", String),
    Column("instrument_id", String),
    Column("quantity", Numeric(28, 6)),
    schema="silver",
)

xentis_positions_raw = Table(
    "xentis_positions_raw", metadata,
    Column("delivery_date", Date, nullable=False),
    Column("file_name", String, nullable=False),
    Column("received_at", DateTime, nullable=False),
    Column("line_no", Integer, nullable=False),
    Column("payload", String, nullable=False),
    schema="bronze",
)

reconcile_proposal = Table(
    "reconcile_proposal", metadata,
    Column("job_id", String, nullable=False),
    Column("instrument_id", String),
    Column("isin", String),
    Column("action", String, nullable=False),
    Column("reason", String),
    Column("order_id", String),
    schema="app",
)

dq_exception = Table(
    "dq_exception", metadata,
    Column("exception_id", String, primary_key=True),
    Column("type", String, nullable=False),
    Column("instrument_id", String, nullable=False),
    Column("field", String, nullable=False),
    Column("value_date", Date, nullable=False),
    Column("detail", String),
    Column("detected_at", DateTime, nullable=False),
    Column("status", String, nullable=False),
    Column("handled_by", String),
    Column("handled_at", DateTime),
    schema="gold",
)

timeseries_value = Table(
    "timeseries_value", metadata,
    Column("instrument_id", String, nullable=False),
    Column("field", String, nullable=False),
    Column("value_date", Date, nullable=False),
    Column("value_num", Numeric(28, 10)),
    Column("value_str", String),
    Column("recorded_at", DateTime, nullable=False),
    Column("superseded_at", DateTime),
    Column("bbg_request_id", String, nullable=False),
    Column("source", String, nullable=False, default="BLOOMBERG"),
    schema="silver",
)

issuer = Table(
    "issuer", metadata,
    Column("issuer_id", String, primary_key=True),
    Column("name", String),
    Column("lei", String),
    Column("country", String),
    Column("industry", String),
    schema="silver",
)

security = Table(
    "security", metadata,
    Column("instrument_id", String, primary_key=True),
    Column("issuer_id", String),
    Column("security_type", String),
    Column("currency", String),
    Column("composite_figi", String),
    Column("share_class_figi", String),
    schema="silver",
)

listing = Table(
    "listing", metadata,
    Column("figi", String, primary_key=True),
    Column("instrument_id", String, nullable=False),
    Column("exchange_code", String),
    Column("ticker", String),
    Column("currency", String),
    Column("is_primary", Boolean),
    schema="silver",
)

identifier_xref = Table(
    "identifier_xref", metadata,
    Column("scheme", String, primary_key=True),
    Column("value", String, primary_key=True),
    Column("source", String, primary_key=True),
    Column("instrument_id", String, nullable=False),
    Column("first_seen", DateTime),
    Column("last_seen", DateTime),
    schema="silver",
)

SCHEMAS = ("app", "bronze", "gold", "silver")


def make_engine(url: str) -> Engine:
    """SQLite kennt keine Schemas: dort landen alle Tabellen im Standardschema."""
    if url.startswith("sqlite"):
        kwargs = {
            "connect_args": {"check_same_thread": False},
            "execution_options": {"schema_translate_map": {s: None for s in SCHEMAS}},
        }
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
        engine = create_engine(url, **kwargs)
        metadata.create_all(engine)
        return engine
    # Auf Databricks legt db/ddl.sql die Tabellen an.
    return create_engine(url)
