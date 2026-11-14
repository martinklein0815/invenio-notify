#  Copyright (C) 2025-2026 Cottage Labs.
#
#  Invenio-Notify is free software; you can redistribute it and/or modify
#  it under the terms of the MIT License; see LICENSE file for more details.

"""Add review requests: request type on requests, capabilities on actors."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '1794700000'
down_revision = '1775041118'
branch_labels = ()
depends_on = None


def upgrade():
    """Upgrade database."""
    op.add_column(
        'notify_endorsement_request',
        sa.Column('request_type', sa.Text(), nullable=False, server_default='endorsement'),
    )
    op.add_column(
        'notify_actor',
        sa.Column(
            'capabilities',
            sa.JSON().with_variant(postgresql.JSONB(none_as_null=True), 'postgresql'),
            nullable=True,
        ),
    )


def downgrade():
    """Downgrade database."""
    op.drop_column('notify_actor', 'capabilities')
    op.drop_column('notify_endorsement_request', 'request_type')
