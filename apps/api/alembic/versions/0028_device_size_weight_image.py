"""Weight, size and one cover picture on a device (B2b-2 part 5).

`weight_kg NUMERIC(8,3)`, `length_cm`/`width_cm`/`height_cm NUMERIC(8,1)`,
all nullable and never negative (one CHECK), metric only. `image_s3_key`
holds the key of the device's ONE cover picture; its thumbnail is stored
beside it under a derived key. SCOPE §4.4.1 wants several pictures per
device later — that becomes a table in a later migration.

No data migration: every new column starts empty.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028_device_size_weight_image"
down_revision: Union[str, Sequence[str], None] = "0027_device_alternatives"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("weight_kg", sa.Numeric(8, 3), nullable=True))
    op.add_column("devices", sa.Column("length_cm", sa.Numeric(8, 1), nullable=True))
    op.add_column("devices", sa.Column("width_cm", sa.Numeric(8, 1), nullable=True))
    op.add_column("devices", sa.Column("height_cm", sa.Numeric(8, 1), nullable=True))
    op.add_column("devices", sa.Column("image_s3_key", sa.String(512), nullable=True))
    op.create_check_constraint(
        "ck_devices_size_weight",
        "devices",
        "(weight_kg IS NULL OR weight_kg >= 0) AND (length_cm IS NULL OR length_cm >= 0) "
        "AND (width_cm IS NULL OR width_cm >= 0) AND (height_cm IS NULL OR height_cm >= 0)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_devices_size_weight", "devices", type_="check")
    op.drop_column("devices", "image_s3_key")
    op.drop_column("devices", "height_cm")
    op.drop_column("devices", "width_cm")
    op.drop_column("devices", "length_cm")
    op.drop_column("devices", "weight_kg")
