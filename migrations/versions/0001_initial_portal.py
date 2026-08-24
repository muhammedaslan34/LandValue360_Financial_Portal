"""Initial LandValue360 Client Portal schema."""
from alembic import op
from landvalue360_portal.database import Base
from landvalue360_portal import models  # noqa
revision = "0001_initial_portal"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    Base.metadata.create_all(bind=op.get_bind())

def downgrade():
    Base.metadata.drop_all(bind=op.get_bind())
