CRM_TAG = "CRM"
FINANCE_TAG = "Finance"
PROJECTS_TAG = "Projects"
SETTINGS_TAG = "Settings"

HEALTH_PATHS = frozenset({"/health", "/api/health", "/metrics"})

# Studio payroll (also in app.services.payroll)
WORK_HOURS_PER_DAY = 8
WORK_DAYS_PER_MONTH = 26
HOURS_PER_MONTH = WORK_HOURS_PER_DAY * WORK_DAYS_PER_MONTH  # 208

