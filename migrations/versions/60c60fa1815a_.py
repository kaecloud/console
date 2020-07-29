"""empty message

Revision ID: 60c60fa1815a
Revises: b11ec42d7f23
Create Date: 2020-06-16 06:41:50.787927

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '60c60fa1815a'
down_revision = 'b11ec42d7f23'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('app', 'rank')
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('app', sa.Column('rank', mysql.INTEGER(display_width=11), autoincrement=False, nullable=True))
    # ### end Alembic commands ###