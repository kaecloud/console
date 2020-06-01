"""empty message

Revision ID: cdc112eeed41
Revises: 65fc0ecca1cf
Create Date: 2020-05-25 10:11:18.084093

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'cdc112eeed41'
down_revision = '65fc0ecca1cf'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint('group_role_binding_ibfk_1', 'group_role_binding', type_='foreignkey')
    op.create_foreign_key(None, 'group_role_binding', 'role', ['role_id'], ['id'], ondelete='CASCADE')
    op.drop_constraint('user_role_binding_ibfk_1', 'user_role_binding', type_='foreignkey')
    op.create_foreign_key(None, 'user_role_binding', 'role', ['role_id'], ['id'], ondelete='CASCADE')
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(None, 'user_role_binding', type_='foreignkey')
    op.create_foreign_key('user_role_binding_ibfk_1', 'user_role_binding', 'role', ['role_id'], ['id'])
    op.drop_constraint(None, 'group_role_binding', type_='foreignkey')
    op.create_foreign_key('group_role_binding_ibfk_1', 'group_role_binding', 'role', ['role_id'], ['id'])
    # ### end Alembic commands ###