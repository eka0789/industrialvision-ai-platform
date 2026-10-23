"""Alembic migration template."""
from mako import exceptions
from alembic import op

revision = ${repr(revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade():
    ${Markup(exceptions._expect_content_in_mako_template())}


def downgrade():
    ${Markup(exceptions._expect_content_in_mako_template())}
