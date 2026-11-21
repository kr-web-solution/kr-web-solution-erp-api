"""Reporting views for dashboard / AR / spend. Applied by Alembic baseline."""

VIEW_STATEMENTS = [
    """
    CREATE OR REPLACE VIEW v_invoice_monthly_billed AS
    SELECT
      to_char(issue_date, 'YYYY-MM') AS month_key,
      (date_trunc('month', issue_date::timestamp))::date AS month_start,
      ledger,
      COUNT(*)::bigint AS invoice_count,
      COALESCE(SUM(total_paise), 0)::bigint AS billed_paise
    FROM invoices
    WHERE deleted_at IS NULL
      AND status NOT IN ('draft', 'cancelled', 'void')
    GROUP BY 1, 2, 3
    """,
    """
    CREATE OR REPLACE VIEW v_invoice_monthly_collected AS
    SELECT
      to_char((paid_at AT TIME ZONE 'UTC')::date, 'YYYY-MM') AS month_key,
      (date_trunc('month', (paid_at AT TIME ZONE 'UTC')))::date AS month_start,
      ledger,
      COUNT(*)::bigint AS invoice_count,
      COALESCE(SUM(total_paise), 0)::bigint AS collected_paise
    FROM invoices
    WHERE deleted_at IS NULL AND status = 'paid' AND paid_at IS NOT NULL
    GROUP BY 1, 2, 3
    """,
    """
    CREATE OR REPLACE VIEW v_company_expense_monthly AS
    SELECT
      to_char(expense_date, 'YYYY-MM') AS month_key,
      (date_trunc('month', expense_date::timestamp))::date AS month_start,
      section,
      COUNT(*)::bigint AS expense_count,
      COALESCE(SUM(amount_paise), 0)::bigint AS spend_paise
    FROM company_expenses
    WHERE deleted_at IS NULL
    GROUP BY 1, 2, 3
    """,
    """
    CREATE OR REPLACE VIEW v_client_ar_summary AS
    SELECT
      c.id AS client_id,
      c.name AS client_name,
      COUNT(i.id) FILTER (
        WHERE i.status IN ('sent', 'overdue', 'proof_pending')
      )::bigint AS open_invoice_count,
      COALESCE(SUM(
        GREATEST(i.total_paise - COALESCE(i.amount_paid_paise, 0), 0)
      ) FILTER (
        WHERE i.status IN ('sent', 'overdue', 'proof_pending')
      ), 0)::bigint AS outstanding_paise,
      COALESCE(SUM(i.amount_paid_paise) FILTER (
        WHERE i.status NOT IN ('draft', 'cancelled', 'void')
      ), 0)::bigint AS paid_paise,
      COALESCE(SUM(i.total_paise) FILTER (
        WHERE i.status NOT IN ('draft', 'cancelled', 'void')
      ), 0)::bigint AS billed_paise
    FROM clients c
    LEFT JOIN invoices i ON i.client_id = c.id AND i.deleted_at IS NULL
    WHERE c.deleted_at IS NULL
    GROUP BY c.id, c.name
    """,
    """
    CREATE OR REPLACE VIEW v_dashboard_founder_kpis AS
    SELECT
      (SELECT COUNT(*) FROM projects WHERE deleted_at IS NULL AND status = 'running')::bigint AS running_projects,
      (SELECT COUNT(*) FROM leads WHERE deleted_at IS NULL AND status IN ('new', 'contacted', 'proposal'))::bigint AS open_leads,
      (SELECT COALESCE(SUM(GREATEST(total_paise - COALESCE(amount_paid_paise, 0), 0)), 0)
         FROM invoices
         WHERE deleted_at IS NULL AND status IN ('sent', 'overdue', 'proof_pending'))::bigint AS outstanding_paise,
      (SELECT COUNT(*) FROM invoices WHERE deleted_at IS NULL AND status = 'overdue')::bigint AS overdue_invoices,
      (SELECT COUNT(*) FROM invoices WHERE deleted_at IS NULL AND email_status = 'failed')::bigint AS failed_email_invoices,
      (SELECT COUNT(*) FROM late_fee_actions WHERE deleted_at IS NULL AND status = 'pending')::bigint AS pending_late_fees,
      (SELECT COUNT(*) FROM engagements WHERE deleted_at IS NULL AND status = 'active')::bigint AS active_engagements,
      (SELECT COALESCE(SUM(amount_paise), 0) FROM company_expenses
         WHERE deleted_at IS NULL AND expense_date >= (date_trunc('month', CURRENT_DATE))::date)::bigint AS spend_this_month_paise
    """,
    """
    CREATE OR REPLACE VIEW v_project_status_counts AS
    SELECT
      status,
      COUNT(*)::bigint AS project_count
    FROM projects
    WHERE deleted_at IS NULL
    GROUP BY status
    """,
    """
    CREATE OR REPLACE VIEW v_overdue_invoices AS
    SELECT
      i.id,
      i.invoice_number,
      i.client_id,
      c.name AS client_name,
      i.total_paise,
      i.due_date,
      i.status,
      i.ledger,
      (CURRENT_DATE - i.due_date) AS days_overdue
    FROM invoices i
    JOIN clients c ON c.id = i.client_id AND c.deleted_at IS NULL
    WHERE i.deleted_at IS NULL
      AND i.status IN ('sent', 'overdue')
      AND i.due_date < CURRENT_DATE
    """,
    """
    CREATE OR REPLACE VIEW v_staff_open_work AS
    SELECT
      u.id AS user_id,
      u.full_name,
      u.department,
      u.role,
      u.party_type,
      COUNT(t.id) FILTER (WHERE t.deleted_at IS NULL AND t.status IN ('todo', 'doing'))::bigint AS open_tasks,
      COUNT(t.id) FILTER (WHERE t.deleted_at IS NULL AND t.status = 'doing')::bigint AS doing_tasks,
      COUNT(lr.id) FILTER (WHERE lr.deleted_at IS NULL AND lr.status = 'pending')::bigint AS pending_leaves,
      COUNT(ec.id) FILTER (WHERE ec.deleted_at IS NULL AND ec.status = 'pending')::bigint AS pending_expense_claims
    FROM users u
    LEFT JOIN work_tasks t ON t.assignee_user_id = u.id
    LEFT JOIN leave_requests lr ON lr.user_id = u.id
    LEFT JOIN expense_claims ec ON ec.user_id = u.id
    WHERE u.deleted_at IS NULL
      AND u.party_type = 'staff'
      AND u.is_active = true
    GROUP BY u.id, u.full_name, u.department, u.role, u.party_type
    """,
    """
    CREATE OR REPLACE VIEW v_users_directory AS
    SELECT
      u.id AS user_id,
      u.email,
      u.full_name,
      u.phone,
      u.designation,
      u.role AS role_code,
      u.role_id,
      r.name AS role_name,
      u.party_type,
      u.is_active,
      u.client_id,
      c.name AS client_name,
      u.lead_id,
      l.company AS lead_company,
      l.name AS lead_name,
      l.status AS lead_status,
      u.created_at
    FROM users u
    JOIN roles r ON r.id = u.role_id AND r.deleted_at IS NULL
    LEFT JOIN clients c ON c.id = u.client_id AND c.deleted_at IS NULL
    LEFT JOIN leads l ON l.id = u.lead_id AND l.deleted_at IS NULL
    WHERE u.deleted_at IS NULL
    """,
]

DROP_VIEW_STATEMENTS = [
    "DROP VIEW IF EXISTS v_users_directory CASCADE",
    "DROP VIEW IF EXISTS v_staff_open_work CASCADE",
    "DROP VIEW IF EXISTS v_overdue_invoices CASCADE",
    "DROP VIEW IF EXISTS v_project_status_counts CASCADE",
    "DROP VIEW IF EXISTS v_dashboard_founder_kpis CASCADE",
    "DROP VIEW IF EXISTS v_client_ar_summary CASCADE",
    "DROP VIEW IF EXISTS v_company_expense_monthly CASCADE",
    "DROP VIEW IF EXISTS v_invoice_monthly_collected CASCADE",
    "DROP VIEW IF EXISTS v_invoice_monthly_billed CASCADE",
]
