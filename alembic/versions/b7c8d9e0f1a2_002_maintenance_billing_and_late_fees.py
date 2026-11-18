"""002_maintenance_billing_and_late_fees

Revision ID: b7c8d9e0f1a2
Revises: aadf61f35e83
Create Date: 2026-11-04 16:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c8d9e0f1a2'
down_revision: Union[str, Sequence[str], None] = 'aadf61f35e83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Extend recurring_invoices
    op.add_column('recurring_invoices', sa.Column('amount_paise', sa.BigInteger(), server_default='0', nullable=False))
    op.add_column('recurring_invoices', sa.Column('send_day', sa.Integer(), server_default='1', nullable=False))
    op.add_column('recurring_invoices', sa.Column('due_after_days', sa.Integer(), server_default='5', nullable=False))
    op.add_column('recurring_invoices', sa.Column('reminder_every_days', sa.Integer(), server_default='2', nullable=False))
    op.add_column('recurring_invoices', sa.Column('starts_on', sa.Date(), server_default=sa.text('CURRENT_DATE'), nullable=False))
    op.add_column('recurring_invoices', sa.Column('ends_on', sa.Date(), nullable=True))

    # 2. Extend invoices
    op.add_column('invoices', sa.Column('recurring_id', sa.String(length=36), nullable=True))
    op.add_column('invoices', sa.Column('period_key', sa.String(length=20), nullable=True))
    op.add_column('invoices', sa.Column('parent_invoice_id', sa.String(length=36), nullable=True))
    op.add_column('invoices', sa.Column('reminder_count', sa.Integer(), server_default='0', nullable=False))
    op.add_column('invoices', sa.Column('last_reminder_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('invoices', sa.Column('overdue_notified_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('invoices', sa.Column('email_status', sa.String(length=20), server_default='pending', nullable=False))
    op.add_column('invoices', sa.Column('email_error', sa.Text(), nullable=True))
    op.add_column('invoices', sa.Column('email_retry_count', sa.Integer(), server_default='0', nullable=False))
    op.add_column('invoices', sa.Column('last_emailed_at', sa.DateTime(timezone=True), nullable=True))

    op.create_foreign_key('fk_invoices_recurring_id', 'invoices', 'recurring_invoices', ['recurring_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_invoices_parent_invoice_id', 'invoices', 'invoices', ['parent_invoice_id'], ['id'], ondelete='SET NULL')
    op.create_index(op.f('ix_invoices_recurring_id'), 'invoices', ['recurring_id'], unique=False)
    op.create_index(op.f('ix_invoices_period_key'), 'invoices', ['period_key'], unique=False)
    op.create_index(op.f('ix_invoices_email_status'), 'invoices', ['email_status'], unique=False)
    op.create_unique_constraint('uq_invoice_recurring_period', 'invoices', ['recurring_id', 'period_key'])

    # 3. Create late_fee_actions table
    op.create_table(
        'late_fee_actions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('invoice_id', sa.String(length=36), nullable=False),
        sa.Column('fee_paise', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('fee_type', sa.String(length=20), server_default='fixed', nullable=False),
        sa.Column('fee_value', sa.Numeric(precision=10, scale=2), server_default='0.00', nullable=False),
        sa.Column('reason', sa.String(length=255), server_default='Payment not received by due date', nullable=False),
        sa.Column('send_on', sa.Date(), nullable=False),
        sa.Column('repeat_every_days', sa.Integer(), nullable=True),
        sa.Column('repeat_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
        sa.Column('decided_by', sa.String(length=36), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('late_fee_invoice_id', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['decided_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['late_fee_invoice_id'], ['invoices.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_late_fee_actions_id'), 'late_fee_actions', ['id'], unique=False)
    op.create_index(op.f('ix_late_fee_actions_invoice_id'), 'late_fee_actions', ['invoice_id'], unique=False)
    op.create_index(op.f('ix_late_fee_actions_status'), 'late_fee_actions', ['status'], unique=False)

    # 4. Extend settings
    op.add_column('settings', sa.Column('invoice_cc_email', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('settings', 'invoice_cc_email')
    op.drop_index(op.f('ix_late_fee_actions_status'), table_name='late_fee_actions')
    op.drop_index(op.f('ix_late_fee_actions_invoice_id'), table_name='late_fee_actions')
    op.drop_index(op.f('ix_late_fee_actions_id'), table_name='late_fee_actions')
    op.drop_table('late_fee_actions')

    op.drop_constraint('uq_invoice_recurring_period', 'invoices', type_='unique')
    op.drop_index(op.f('ix_invoices_email_status'), table_name='invoices')
    op.drop_index(op.f('ix_invoices_period_key'), table_name='invoices')
    op.drop_index(op.f('ix_invoices_recurring_id'), table_name='invoices')
    op.drop_constraint('fk_invoices_parent_invoice_id', 'invoices', type_='foreignkey')
    op.drop_constraint('fk_invoices_recurring_id', 'invoices', type_='foreignkey')

    op.drop_column('invoices', 'last_emailed_at')
    op.drop_column('invoices', 'email_retry_count')
    op.drop_column('invoices', 'email_error')
    op.drop_column('invoices', 'email_status')
    op.drop_column('invoices', 'overdue_notified_at')
    op.drop_column('invoices', 'last_reminder_at')
    op.drop_column('invoices', 'reminder_count')
    op.drop_column('invoices', 'parent_invoice_id')
    op.drop_column('invoices', 'period_key')
    op.drop_column('invoices', 'recurring_id')

    op.drop_column('recurring_invoices', 'ends_on')
    op.drop_column('recurring_invoices', 'starts_on')
    op.drop_column('recurring_invoices', 'reminder_every_days')
    op.drop_column('recurring_invoices', 'due_after_days')
    op.drop_column('recurring_invoices', 'send_day')
    op.drop_column('recurring_invoices', 'amount_paise')

