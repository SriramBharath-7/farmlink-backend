"""transaction state machine trigger

Enforces blueprint section 28's rule -- "A transaction must never jump
randomly between states" -- as an actual database trigger, not just a
docstring convention that the application layer could bypass or forget.

Allowed transitions (linear happy path + CANCELLED as an exit valve from
any non-terminal state, since real deals do fall through):

    OFFER_ACCEPTED     -> PAYMENT_INITIATED, CANCELLED
    PAYMENT_INITIATED  -> LOGISTICS_ARRANGED, CANCELLED
    LOGISTICS_ARRANGED -> IN_TRANSIT, CANCELLED
    IN_TRANSIT         -> DELIVERED, CANCELLED
    DELIVERED          -> PAYMENT_CONFIRMED, CANCELLED
    PAYMENT_CONFIRMED  -> COMPLETED
    COMPLETED          -> (terminal, no transitions out)
    CANCELLED          -> (terminal, no transitions out)

Revision ID: 0002_txn_fsm
Revises: e2dd6d9a2b45
"""
from alembic import op


revision = "0002_txn_fsm"
down_revision = "e2dd6d9a2b45"
branch_labels = None
depends_on = None


TRIGGER_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION enforce_transaction_status_transition()
RETURNS TRIGGER AS $$
DECLARE
    allowed BOOLEAN := FALSE;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status IS DISTINCT FROM 'OFFER_ACCEPTED' THEN
            RAISE EXCEPTION 'New transactions must start in OFFER_ACCEPTED, got %', NEW.status;
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status = NEW.status THEN
        RETURN NEW; -- no-op update to some other column is fine
    END IF;

    allowed := CASE OLD.status
        WHEN 'OFFER_ACCEPTED'     THEN NEW.status IN ('PAYMENT_INITIATED', 'CANCELLED')
        WHEN 'PAYMENT_INITIATED'  THEN NEW.status IN ('LOGISTICS_ARRANGED', 'CANCELLED')
        WHEN 'LOGISTICS_ARRANGED' THEN NEW.status IN ('IN_TRANSIT', 'CANCELLED')
        WHEN 'IN_TRANSIT'         THEN NEW.status IN ('DELIVERED', 'CANCELLED')
        WHEN 'DELIVERED'          THEN NEW.status IN ('PAYMENT_CONFIRMED', 'CANCELLED')
        WHEN 'PAYMENT_CONFIRMED'  THEN NEW.status IN ('COMPLETED')
        ELSE FALSE  -- COMPLETED and CANCELLED are terminal
    END;

    IF NOT allowed THEN
        RAISE EXCEPTION 'Illegal transaction status transition: % -> %', OLD.status, NEW.status;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

TRIGGER_SQL = """
CREATE TRIGGER trg_transaction_status_transition
BEFORE INSERT OR UPDATE OF status ON transactions
FOR EACH ROW
EXECUTE FUNCTION enforce_transaction_status_transition();
"""


def upgrade():
    op.execute(TRIGGER_FUNCTION_SQL)
    op.execute(TRIGGER_SQL)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_transaction_status_transition ON transactions;")
    op.execute("DROP FUNCTION IF EXISTS enforce_transaction_status_transition();")
