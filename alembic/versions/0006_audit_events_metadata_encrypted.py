"""Document AES-256-GCM encryption of audit_events.metadata column.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-01

Spec: specs/privacy/db-encryption-at-rest.md
ADR:  ADR-0018 (Database Encryption at Rest)

No schema change is required — the metadata column remains TEXT. This migration
adds a PostgreSQL column comment to make the wire format explicit in the schema
and ensure any future tooling (pg_dump inspection, schema drift checks) can
detect that this column stores encrypted values.

Wire format written by PostgresAuditStorage (with encryption enabled):
    enc:v1:<base64(nonce[12] || ciphertext_with_tag)>

Pre-existing plaintext rows are handled transparently by EncryptedField.decrypt()
passthrough: any value that does not start with 'enc:v1:' is returned as-is.
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        COMMENT ON COLUMN audit_events.metadata IS
        'AES-256-GCM encrypted JSON (enc:v1:<base64> wire format, ADR-0018).
         Pre-existing plaintext rows are readable via the EncryptedField passthrough.';
        """
    )


def downgrade() -> None:
    op.execute("COMMENT ON COLUMN audit_events.metadata IS NULL")
