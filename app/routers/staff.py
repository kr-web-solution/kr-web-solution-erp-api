"""Staff / worker management: directory, tasks, attendance, leave, expenses, docs, payslips."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import extract, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..core.dependencies import get_db, is_privileged, require_founder_ceo, require_staff
from ..models import (
    Announcement,
    AttendanceDay,
    CompanyHoliday,
    ExpenseClaim,
    LeaveRequest,
    Payslip,
    Project,
    Settings,
    StaffDocument,
    User,
    WorkTask,
)
from ..schemas import (
    AnnouncementCreate,
    AnnouncementResponse,
    AttendanceCorrectionRequest,
    AttendanceResponse,
    ExpenseClaimResponse,
    HolidayCreate,
    HolidayResponse,
    LeaveBalanceResponse,
    LeaveRequestCreate,
    LeaveRequestResponse,
    PayslipPublishRequest,
    PayslipResponse,
    StaffDocumentResponse,
    StaffHomeResponse,
    StaffInboxResponse,
    StaffProfileResponse,
    StaffProfileUpdate,
    WorkTaskCreate,
    WorkTaskResponse,
    WorkTaskUpdate,
)
from ..services.pdf import generate_payslip_pdf
from ..services.payroll import (
    HOURS_PER_MONTH,
    WORK_HOURS_PER_DAY,
    hourly_from_monthly,
    leave_days,
    leave_hours,
    leave_hours_in_month,
    parse_month_key,
    resolve_hourly_rate_paise,
    unpaid_deduction_paise,
)
from ..services.staff_files import (
    DOC_KINDS,
    read_object,
    read_staff_document,
    save_expense_receipt,
    save_payslip_pdf,
    save_staff_upload,
)

router = APIRouter(prefix="/staff", tags=["Staff"])

TASK_STATUSES = {"todo", "doing", "done"}
LEAVE_TYPES = {"casual", "sick", "unpaid"}
CORRECTION_REASONS = {"missed", "wfh", "on_duty"}
EXPENSE_CATEGORIES = {"travel", "food", "other"}
DECISION_STATUSES = {"approved", "rejected"}


# ----------------- helpers -----------------


def _staff_roles_filter():
    return User.role.in_(["worker", "admin", "founder_ceo"])


async def _direct_reports(db: AsyncSession, manager_id: str) -> List[User]:
    result = await db.execute(
        select(User).where(
            User.reports_to_user_id == manager_id,
            User.is_active == True,  # noqa: E712
            _staff_roles_filter(),
        )
    )
    return list(result.scalars().all())


async def _is_manager(db: AsyncSession, user: User) -> bool:
    if is_privileged(user.role):
        return True
    result = await db.execute(
        select(User.id).where(User.reports_to_user_id == user.id).limit(1)
    )
    return result.scalar_one_or_none() is not None


async def _can_manage_user(db: AsyncSession, actor: User, target_id: str) -> bool:
    if is_privileged(actor.role):
        return True
    if actor.id == target_id:
        return True
    result = await db.execute(
        select(User.id).where(User.id == target_id, User.reports_to_user_id == actor.id)
    )
    return result.scalar_one_or_none() is not None


async def _visible_staff_ids(db: AsyncSession, actor: User) -> Optional[List[str]]:
    """None means all staff (founder/admin). Otherwise the actor + direct reports."""
    if is_privileged(actor.role):
        return None
    reports = await _direct_reports(db, actor.id)
    return [actor.id] + [r.id for r in reports]


def _leave_days(req: LeaveRequest) -> float:
    return leave_days(req)


def _leave_hours(req: LeaveRequest) -> float:
    return leave_hours(req)


async def _leave_balance(db: AsyncSession, user: User, year: Optional[int] = None) -> LeaveBalanceResponse:
    year = year or date.today().year
    result = await db.execute(
        select(LeaveRequest).where(
            LeaveRequest.user_id == user.id,
            LeaveRequest.status == "approved",
            extract("year", LeaveRequest.start_date) == year,
        )
    )
    rows = list(result.scalars().all())
    casual_used = sum(_leave_days(r) for r in rows if r.leave_type == "casual")
    sick_used = sum(_leave_days(r) for r in rows if r.leave_type == "sick")
    unpaid_used = sum(_leave_days(r) for r in rows if r.leave_type == "unpaid")
    casual_used_h = sum(_leave_hours(r) for r in rows if r.leave_type == "casual")
    sick_used_h = sum(_leave_hours(r) for r in rows if r.leave_type == "sick")
    unpaid_used_h = sum(_leave_hours(r) for r in rows if r.leave_type == "unpaid")
    casual_all = user.casual_leave_days or 12
    sick_all = user.sick_leave_days or 6
    casual_all_h = float(casual_all * WORK_HOURS_PER_DAY)
    sick_all_h = float(sick_all * WORK_HOURS_PER_DAY)
    return LeaveBalanceResponse(
        casual_allowance=casual_all,
        casual_used=casual_used,
        casual_remaining=max(0.0, casual_all - casual_used),
        sick_allowance=sick_all,
        sick_used=sick_used,
        sick_remaining=max(0.0, sick_all - sick_used),
        unpaid_used=unpaid_used,
        casual_allowance_hours=casual_all_h,
        casual_used_hours=casual_used_h,
        casual_remaining_hours=max(0.0, casual_all_h - casual_used_h),
        sick_allowance_hours=sick_all_h,
        sick_used_hours=sick_used_h,
        sick_remaining_hours=max(0.0, sick_all_h - sick_used_h),
        unpaid_used_hours=unpaid_used_h,
    )


async def _unpaid_hours_for_month(
    db: AsyncSession, user_id: str, month_key: str
) -> float:
    year, month = parse_month_key(month_key)
    result = await db.execute(
        select(LeaveRequest).where(
            LeaveRequest.user_id == user_id,
            LeaveRequest.status == "approved",
            LeaveRequest.leave_type == "unpaid",
        )
    )
    return sum(leave_hours_in_month(r, year, month) for r in result.scalars().all())


async def _manager_name(db: AsyncSession, reports_to: Optional[str]) -> Optional[str]:
    if not reports_to:
        return None
    result = await db.execute(select(User.full_name).where(User.id == reports_to))
    return result.scalar_one_or_none()


async def _profile_response(
    db: AsyncSession, user: User, *, with_balance: bool = False
) -> StaffProfileResponse:
    balance = await _leave_balance(db, user) if with_balance else None
    return StaffProfileResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        department=user.department,
        job_title=user.job_title,
        reports_to_user_id=user.reports_to_user_id,
        reports_to_name=await _manager_name(db, user.reports_to_user_id),
        joined_on=user.joined_on,
        phone=user.phone,
        address=user.address,
        bank_account=user.bank_account,
        bank_ifsc=user.bank_ifsc,
        pending_bank_account=user.pending_bank_account,
        pending_bank_ifsc=user.pending_bank_ifsc,
        monthly_gross_paise=user.monthly_gross_paise,
        hourly_rate_paise=user.hourly_rate_paise,
        casual_leave_days=user.casual_leave_days or 12,
        sick_leave_days=user.sick_leave_days or 6,
        leave_balance=balance,
        created_at=user.created_at,
    )


def _task_response(task: WorkTask) -> WorkTaskResponse:
    return WorkTaskResponse(
        id=task.id,
        work_date=task.work_date,
        title=task.title,
        project_id=task.project_id,
        project_name=task.project.name if task.project else None,
        assignee_user_id=task.assignee_user_id,
        assignee_name=task.assignee.full_name if task.assignee else None,
        created_by_user_id=task.created_by_user_id,
        status=task.status,
        created_at=task.created_at,
    )


def _attendance_response(row: AttendanceDay) -> AttendanceResponse:
    return AttendanceResponse(
        id=row.id,
        user_id=row.user_id,
        user_name=row.user.full_name if row.user else None,
        work_date=row.work_date,
        check_in_at=row.check_in_at,
        check_out_at=row.check_out_at,
        correction_reason=row.correction_reason,
        correction_note=row.correction_note,
        correction_status=row.correction_status,
    )


def _leave_response(row: LeaveRequest) -> LeaveRequestResponse:
    return LeaveRequestResponse(
        id=row.id,
        user_id=row.user_id,
        user_name=row.user.full_name if row.user else None,
        leave_type=row.leave_type,
        start_date=row.start_date,
        end_date=row.end_date,
        half_day=row.half_day,
        reason=row.reason,
        status=row.status,
        decided_by_user_id=row.decided_by_user_id,
        decided_at=row.decided_at,
        created_at=row.created_at,
    )


def _expense_response(row: ExpenseClaim) -> ExpenseClaimResponse:
    return ExpenseClaimResponse(
        id=row.id,
        user_id=row.user_id,
        user_name=row.user.full_name if row.user else None,
        expense_date=row.expense_date,
        category=row.category,
        amount_paise=row.amount_paise,
        note=row.note,
        receipt_object_key=row.receipt_object_key,
        receipt_original_filename=row.receipt_original_filename,
        status=row.status,
        decided_by_user_id=row.decided_by_user_id,
        decided_at=row.decided_at,
        paid_at=row.paid_at,
        created_at=row.created_at,
    )


def _payslip_response(row: Payslip) -> PayslipResponse:
    return PayslipResponse(
        id=row.id,
        user_id=row.user_id,
        user_name=row.user.full_name if row.user else None,
        month_key=row.month_key,
        gross_paise=row.gross_paise,
        deductions_paise=row.deductions_paise,
        net_paise=row.net_paise,
        note=row.note,
        created_at=row.created_at,
    )


def _announcement_response(row: Announcement) -> AnnouncementResponse:
    return AnnouncementResponse(
        id=row.id,
        title=row.title,
        body=row.body,
        created_by_user_id=row.created_by_user_id,
        created_by_name=row.created_by.full_name if row.created_by else None,
        created_at=row.created_at,
    )


# ----------------- profile / directory -----------------


@router.get("/me", response_model=StaffHomeResponse)
async def staff_home(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    today = date.today()
    profile = await _profile_response(db, current_user, with_balance=True)

    tasks_q = await db.execute(
        select(WorkTask)
        .options(selectinload(WorkTask.project), selectinload(WorkTask.assignee))
        .where(WorkTask.assignee_user_id == current_user.id, WorkTask.work_date == today)
        .order_by(WorkTask.created_at.asc())
    )
    today_tasks = [_task_response(t) for t in tasks_q.scalars().all()]

    att_q = await db.execute(
        select(AttendanceDay)
        .options(selectinload(AttendanceDay.user))
        .where(AttendanceDay.user_id == current_user.id, AttendanceDay.work_date == today)
    )
    att = att_q.scalars().first()
    today_attendance = _attendance_response(att) if att else None

    user_with_projects = (
        await db.execute(
            select(User)
            .options(selectinload(User.assigned_projects))
            .where(User.id == current_user.id)
        )
    ).scalars().first()
    projects = [
        {"id": p.id, "name": p.name, "status": p.status}
        for p in sorted(user_with_projects.assigned_projects or [], key=lambda p: p.name)
    ] if user_with_projects else []

    exp_q = await db.execute(
        select(ExpenseClaim)
        .options(selectinload(ExpenseClaim.user))
        .where(
            ExpenseClaim.user_id == current_user.id,
            ExpenseClaim.status.in_(["pending", "approved"]),
        )
        .order_by(ExpenseClaim.created_at.desc())
        .limit(10)
    )
    open_expenses = [_expense_response(e) for e in exp_q.scalars().all()]

    ann_q = await db.execute(
        select(Announcement)
        .options(selectinload(Announcement.created_by))
        .order_by(Announcement.created_at.desc())
        .limit(1)
    )
    ann = ann_q.scalars().first()

    pay_q = await db.execute(
        select(Payslip)
        .options(selectinload(Payslip.user))
        .where(Payslip.user_id == current_user.id)
        .order_by(Payslip.month_key.desc())
        .limit(1)
    )
    pay = pay_q.scalars().first()

    return StaffHomeResponse(
        profile=profile,
        today_tasks=today_tasks,
        today_attendance=today_attendance,
        projects=projects,
        leave_balance=profile.leave_balance or await _leave_balance(db, current_user),
        open_expenses=open_expenses,
        latest_announcement=_announcement_response(ann) if ann else None,
        latest_payslip=_payslip_response(pay) if pay else None,
    )


@router.get("/me/profile", response_model=StaffProfileResponse)
async def get_my_profile(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    return await _profile_response(db, current_user, with_balance=True)


@router.patch("/me", response_model=StaffProfileResponse)
async def update_my_profile(
    payload: StaffProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    """Worker can edit phone/address immediately; bank changes wait for approval."""
    if payload.phone is not None:
        current_user.phone = payload.phone.strip() or None
    if payload.address is not None:
        current_user.address = payload.address.strip() or None
    if payload.full_name is not None and payload.full_name.strip():
        current_user.full_name = payload.full_name.strip()

    bank_changed = False
    if payload.bank_account is not None:
        new_acc = payload.bank_account.strip() or None
        if new_acc != current_user.bank_account:
            current_user.pending_bank_account = new_acc
            bank_changed = True
    if payload.bank_ifsc is not None:
        new_ifsc = payload.bank_ifsc.strip().upper() or None
        if new_ifsc != current_user.bank_ifsc:
            current_user.pending_bank_ifsc = new_ifsc
            bank_changed = True
    if bank_changed and is_privileged(current_user.role):
        # Founder/admin editing own bank applies immediately
        if current_user.pending_bank_account is not None:
            current_user.bank_account = current_user.pending_bank_account
            current_user.pending_bank_account = None
        if current_user.pending_bank_ifsc is not None:
            current_user.bank_ifsc = current_user.pending_bank_ifsc
            current_user.pending_bank_ifsc = None

    # Job fields only via admin patch below — ignore here for workers
    await db.flush()
    await db.refresh(current_user)
    return await _profile_response(db, current_user, with_balance=True)


@router.get("", response_model=List[StaffProfileResponse])
async def list_staff(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    """Directory: founder/admin see all; managers see their team (+ self)."""
    visible = await _visible_staff_ids(db, current_user)
    if visible is not None and len(visible) <= 1 and not await _is_manager(db, current_user):
        # Plain worker — only themselves
        return [await _profile_response(db, current_user, with_balance=True)]

    q = select(User).where(_staff_roles_filter(), User.is_active == True)  # noqa: E712
    if visible is not None:
        q = q.where(User.id.in_(visible))
    q = q.order_by(User.department.nulls_last(), User.full_name)
    result = await db.execute(q)
    users = list(result.scalars().all())
    return [await _profile_response(db, u, with_balance=True) for u in users]


@router.patch("/{user_id}", response_model=StaffProfileResponse)
async def update_staff_profile(
    user_id: str,
    payload: StaffProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalars().first()
    if not target or target.role == "client":
        raise HTTPException(status_code=404, detail="Staff member not found")

    if payload.full_name is not None and payload.full_name.strip():
        target.full_name = payload.full_name.strip()
    if payload.department is not None:
        target.department = payload.department.strip() or None
    if payload.job_title is not None:
        target.job_title = payload.job_title.strip() or None
    if payload.reports_to_user_id is not None:
        rid = payload.reports_to_user_id.strip() or None
        if rid == target.id:
            raise HTTPException(status_code=400, detail="Cannot report to yourself")
        if rid:
            mgr = await db.execute(select(User).where(User.id == rid))
            if not mgr.scalars().first():
                raise HTTPException(status_code=400, detail="Manager not found")
        target.reports_to_user_id = rid
    if payload.joined_on is not None:
        target.joined_on = payload.joined_on
    if payload.phone is not None:
        target.phone = payload.phone.strip() or None
    if payload.address is not None:
        target.address = payload.address.strip() or None
    if payload.bank_account is not None:
        target.bank_account = payload.bank_account.strip() or None
        target.pending_bank_account = None
    if payload.bank_ifsc is not None:
        target.bank_ifsc = payload.bank_ifsc.strip().upper() or None
        target.pending_bank_ifsc = None
    if payload.monthly_gross_paise is not None:
        target.monthly_gross_paise = max(0, payload.monthly_gross_paise)
        # Hourly is always derived from monthly (8h × 26d = 208h); ignore client hourly.
        target.hourly_rate_paise = hourly_from_monthly(target.monthly_gross_paise)
    if payload.casual_leave_days is not None:
        target.casual_leave_days = max(0, payload.casual_leave_days)
    if payload.sick_leave_days is not None:
        target.sick_leave_days = max(0, payload.sick_leave_days)

    await db.flush()
    await db.refresh(target)
    return await _profile_response(db, target, with_balance=True)


@router.post("/{user_id}/bank/approve", response_model=StaffProfileResponse)
async def approve_bank_change(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalars().first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.pending_bank_account is None and target.pending_bank_ifsc is None:
        raise HTTPException(status_code=400, detail="No pending bank change")
    if target.pending_bank_account is not None:
        target.bank_account = target.pending_bank_account
        target.pending_bank_account = None
    if target.pending_bank_ifsc is not None:
        target.bank_ifsc = target.pending_bank_ifsc
        target.pending_bank_ifsc = None
    await db.flush()
    await db.refresh(target)
    return await _profile_response(db, target)


@router.post("/{user_id}/bank/reject", response_model=StaffProfileResponse)
async def reject_bank_change(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalars().first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    target.pending_bank_account = None
    target.pending_bank_ifsc = None
    await db.flush()
    await db.refresh(target)
    return await _profile_response(db, target)


# ----------------- tasks -----------------


@router.get("/tasks", response_model=List[WorkTaskResponse])
async def list_tasks(
    work_date: Optional[date] = Query(None),
    mine: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    day = work_date or date.today()
    q = (
        select(WorkTask)
        .options(selectinload(WorkTask.project), selectinload(WorkTask.assignee))
        .where(WorkTask.work_date == day)
    )
    if mine or not await _is_manager(db, current_user):
        q = q.where(WorkTask.assignee_user_id == current_user.id)
    else:
        visible = await _visible_staff_ids(db, current_user)
        if visible is not None:
            q = q.where(WorkTask.assignee_user_id.in_(visible))
    q = q.order_by(WorkTask.created_at.asc())
    result = await db.execute(q)
    return [_task_response(t) for t in result.scalars().all()]


@router.post("/tasks", response_model=WorkTaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: WorkTaskCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    if payload.status not in TASK_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    assignee_id = payload.assignee_user_id or current_user.id
    if not await _can_manage_user(db, current_user, assignee_id):
        raise HTTPException(status_code=403, detail="Cannot assign tasks to this person")
    if payload.project_id:
        proj = await db.execute(select(Project).where(Project.id == payload.project_id))
        if not proj.scalars().first():
            raise HTTPException(status_code=400, detail="Project not found")

    task = WorkTask(
        work_date=payload.work_date,
        title=payload.title.strip(),
        project_id=payload.project_id,
        assignee_user_id=assignee_id,
        created_by_user_id=current_user.id,
        status=payload.status,
    )
    db.add(task)
    await db.flush()
    await db.refresh(task, attribute_names=["project", "assignee"])
    # ensure relationships loaded
    result = await db.execute(
        select(WorkTask)
        .options(selectinload(WorkTask.project), selectinload(WorkTask.assignee))
        .where(WorkTask.id == task.id)
    )
    return _task_response(result.scalars().one())


@router.patch("/tasks/{task_id}", response_model=WorkTaskResponse)
async def update_task(
    task_id: str,
    payload: WorkTaskUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    result = await db.execute(
        select(WorkTask)
        .options(selectinload(WorkTask.project), selectinload(WorkTask.assignee))
        .where(WorkTask.id == task_id)
    )
    task = result.scalars().first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not await _can_manage_user(db, current_user, task.assignee_user_id):
        raise HTTPException(status_code=403, detail="Not allowed")
    if payload.title is not None:
        task.title = payload.title.strip()
    if payload.status is not None:
        if payload.status not in TASK_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid status")
        task.status = payload.status
    if payload.project_id is not None:
        task.project_id = payload.project_id or None
    await db.flush()
    await db.refresh(task)
    result = await db.execute(
        select(WorkTask)
        .options(selectinload(WorkTask.project), selectinload(WorkTask.assignee))
        .where(WorkTask.id == task.id)
    )
    return _task_response(result.scalars().one())


# ----------------- attendance -----------------


@router.post("/attendance/check-in", response_model=AttendanceResponse)
async def check_in(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    today = date.today()
    # Skip marking absent logic on holidays — check-in still allowed but holiday listed separately
    result = await db.execute(
        select(AttendanceDay)
        .options(selectinload(AttendanceDay.user))
        .where(AttendanceDay.user_id == current_user.id, AttendanceDay.work_date == today)
    )
    row = result.scalars().first()
    now = datetime.now(timezone.utc)
    if row and row.check_in_at:
        raise HTTPException(status_code=400, detail="Already checked in today")
    if not row:
        row = AttendanceDay(user_id=current_user.id, work_date=today, check_in_at=now)
        db.add(row)
    else:
        row.check_in_at = now
    await db.flush()
    result = await db.execute(
        select(AttendanceDay)
        .options(selectinload(AttendanceDay.user))
        .where(AttendanceDay.id == row.id)
    )
    return _attendance_response(result.scalars().one())


@router.post("/attendance/check-out", response_model=AttendanceResponse)
async def check_out(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    today = date.today()
    result = await db.execute(
        select(AttendanceDay)
        .options(selectinload(AttendanceDay.user))
        .where(AttendanceDay.user_id == current_user.id, AttendanceDay.work_date == today)
    )
    row = result.scalars().first()
    if not row or not row.check_in_at:
        raise HTTPException(status_code=400, detail="Check in first")
    if row.check_out_at:
        raise HTTPException(status_code=400, detail="Already checked out")
    row.check_out_at = datetime.now(timezone.utc)
    await db.flush()
    result = await db.execute(
        select(AttendanceDay)
        .options(selectinload(AttendanceDay.user))
        .where(AttendanceDay.id == row.id)
    )
    return _attendance_response(result.scalars().one())


@router.get("/attendance", response_model=List[AttendanceResponse])
async def list_attendance(
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    user_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    target_id = user_id or current_user.id
    if not await _can_manage_user(db, current_user, target_id):
        raise HTTPException(status_code=403, detail="Not allowed")
    result = await db.execute(
        select(AttendanceDay)
        .options(selectinload(AttendanceDay.user))
        .where(
            AttendanceDay.user_id == target_id,
            extract("year", AttendanceDay.work_date) == year,
            extract("month", AttendanceDay.work_date) == month,
        )
        .order_by(AttendanceDay.work_date.asc())
    )
    return [_attendance_response(r) for r in result.scalars().all()]


@router.post("/attendance/corrections", response_model=AttendanceResponse, status_code=201)
async def request_correction(
    payload: AttendanceCorrectionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    if payload.reason not in CORRECTION_REASONS:
        raise HTTPException(status_code=400, detail="Invalid correction reason")
    result = await db.execute(
        select(AttendanceDay).where(
            AttendanceDay.user_id == current_user.id,
            AttendanceDay.work_date == payload.work_date,
        )
    )
    row = result.scalars().first()
    if not row:
        row = AttendanceDay(user_id=current_user.id, work_date=payload.work_date)
        db.add(row)
    row.correction_reason = payload.reason
    row.correction_note = payload.note
    row.correction_status = "pending"
    if payload.check_in_at:
        row.check_in_at = payload.check_in_at
    if payload.check_out_at:
        row.check_out_at = payload.check_out_at
    await db.flush()
    result = await db.execute(
        select(AttendanceDay)
        .options(selectinload(AttendanceDay.user))
        .where(AttendanceDay.id == row.id)
    )
    return _attendance_response(result.scalars().one())


@router.post("/attendance/corrections/{attendance_id}/{decision}", response_model=AttendanceResponse)
async def decide_correction(
    attendance_id: str,
    decision: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    if decision not in DECISION_STATUSES:
        raise HTTPException(status_code=400, detail="Decision must be approved or rejected")
    result = await db.execute(
        select(AttendanceDay)
        .options(selectinload(AttendanceDay.user))
        .where(AttendanceDay.id == attendance_id)
    )
    row = result.scalars().first()
    if not row:
        raise HTTPException(status_code=404, detail="Attendance not found")
    if row.correction_status != "pending":
        raise HTTPException(status_code=400, detail="No pending correction")
    if current_user.id == row.user_id and not is_privileged(current_user.role):
        raise HTTPException(status_code=403, detail="Cannot approve your own correction")
    if not await _can_manage_user(db, current_user, row.user_id):
        raise HTTPException(status_code=403, detail="Not allowed")
    row.correction_status = decision
    await db.flush()
    result = await db.execute(
        select(AttendanceDay)
        .options(selectinload(AttendanceDay.user))
        .where(AttendanceDay.id == row.id)
    )
    return _attendance_response(result.scalars().one())


# ----------------- holidays -----------------


@router.get("/holidays", response_model=List[HolidayResponse])
async def list_holidays(
    year: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    y = year or date.today().year
    result = await db.execute(
        select(CompanyHoliday)
        .where(extract("year", CompanyHoliday.holiday_date) == y)
        .order_by(CompanyHoliday.holiday_date.asc())
    )
    return [
        HolidayResponse(id=h.id, holiday_date=h.holiday_date, name=h.name)
        for h in result.scalars().all()
    ]


@router.post("/holidays", response_model=HolidayResponse, status_code=201)
async def create_holiday(
    payload: HolidayCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    existing = await db.execute(
        select(CompanyHoliday).where(CompanyHoliday.holiday_date == payload.holiday_date)
    )
    if existing.scalars().first():
        raise HTTPException(status_code=400, detail="Holiday already exists on this date")
    hol = CompanyHoliday(holiday_date=payload.holiday_date, name=payload.name.strip())
    db.add(hol)
    await db.flush()
    await db.refresh(hol)
    return HolidayResponse(id=hol.id, holiday_date=hol.holiday_date, name=hol.name)


@router.delete("/holidays/{holiday_id}", status_code=200)
async def delete_holiday(
    holiday_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    result = await db.execute(select(CompanyHoliday).where(CompanyHoliday.id == holiday_id))
    hol = result.scalars().first()
    if not hol:
        raise HTTPException(status_code=404, detail="Holiday not found")
    await db.delete(hol)
    return {"message": "Deleted"}


# ----------------- leave -----------------


@router.post("/leave", response_model=LeaveRequestResponse, status_code=201)
async def apply_leave(
    payload: LeaveRequestCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    if payload.leave_type not in LEAVE_TYPES:
        raise HTTPException(status_code=400, detail="Invalid leave type")
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="End date before start date")
    if payload.half_day and payload.start_date != payload.end_date:
        raise HTTPException(status_code=400, detail="Half-day leave must be a single day")

    # Block leave that overlaps a company holiday only for unpaid? Plan: holidays don't use leave.
    # We still allow applying; balance calculation is separate.

    req = LeaveRequest(
        user_id=current_user.id,
        leave_type=payload.leave_type,
        start_date=payload.start_date,
        end_date=payload.end_date,
        half_day=payload.half_day,
        reason=payload.reason,
        status="pending",
    )
    db.add(req)
    await db.flush()
    result = await db.execute(
        select(LeaveRequest)
        .options(selectinload(LeaveRequest.user))
        .where(LeaveRequest.id == req.id)
    )
    return _leave_response(result.scalars().one())


@router.get("/leave", response_model=List[LeaveRequestResponse])
async def list_leave(
    scope: str = Query("mine"),  # mine | team | all
    status_filter: Optional[str] = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    q = select(LeaveRequest).options(selectinload(LeaveRequest.user))
    if scope == "mine":
        q = q.where(LeaveRequest.user_id == current_user.id)
    elif scope == "team":
        visible = await _visible_staff_ids(db, current_user)
        if visible is None:
            pass  # all
        else:
            q = q.where(LeaveRequest.user_id.in_(visible))
    elif scope == "all":
        if not is_privileged(current_user.role):
            raise HTTPException(status_code=403, detail="Not allowed")
    else:
        raise HTTPException(status_code=400, detail="Invalid scope")
    if status_filter:
        q = q.where(LeaveRequest.status == status_filter)
    q = q.order_by(LeaveRequest.start_date.desc())
    result = await db.execute(q)
    return [_leave_response(r) for r in result.scalars().all()]


@router.get("/leave/calendar", response_model=List[LeaveRequestResponse])
async def leave_calendar(
    start: date = Query(...),
    end: date = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    q = (
        select(LeaveRequest)
        .options(selectinload(LeaveRequest.user))
        .where(
            LeaveRequest.status == "approved",
            LeaveRequest.start_date <= end,
            LeaveRequest.end_date >= start,
        )
    )
    visible = await _visible_staff_ids(db, current_user)
    if visible is not None:
        q = q.where(LeaveRequest.user_id.in_(visible))
    q = q.order_by(LeaveRequest.start_date.asc())
    result = await db.execute(q)
    return [_leave_response(r) for r in result.scalars().all()]


@router.post("/leave/{leave_id}/{decision}", response_model=LeaveRequestResponse)
async def decide_leave(
    leave_id: str,
    decision: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    if decision not in DECISION_STATUSES:
        raise HTTPException(status_code=400, detail="Decision must be approved or rejected")
    result = await db.execute(
        select(LeaveRequest)
        .options(selectinload(LeaveRequest.user))
        .where(LeaveRequest.id == leave_id)
    )
    req = result.scalars().first()
    if not req:
        raise HTTPException(status_code=404, detail="Leave request not found")
    if req.status != "pending":
        raise HTTPException(status_code=400, detail="Already decided")
    if current_user.id == req.user_id and not is_privileged(current_user.role):
        raise HTTPException(status_code=403, detail="Cannot approve your own leave")
    if not await _can_manage_user(db, current_user, req.user_id):
        raise HTTPException(status_code=403, detail="Not allowed")
    req.status = decision
    req.decided_by_user_id = current_user.id
    req.decided_at = datetime.now(timezone.utc)
    await db.flush()
    result = await db.execute(
        select(LeaveRequest)
        .options(selectinload(LeaveRequest.user))
        .where(LeaveRequest.id == req.id)
    )
    return _leave_response(result.scalars().one())


# ----------------- expenses -----------------


@router.post("/expenses", response_model=ExpenseClaimResponse, status_code=201)
async def submit_expense(
    expense_date: date = Form(...),
    category: str = Form(...),
    amount_paise: int = Form(...),
    note: Optional[str] = Form(None),
    receipt: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    if category not in EXPENSE_CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid category")
    if amount_paise <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    key = original = content_type = None
    if receipt and receipt.filename:
        key, original, content_type = await save_expense_receipt(current_user.id, receipt)
    claim = ExpenseClaim(
        user_id=current_user.id,
        expense_date=expense_date,
        category=category,
        amount_paise=amount_paise,
        note=note,
        receipt_object_key=key,
        receipt_original_filename=original,
        receipt_content_type=content_type,
        status="pending",
    )
    db.add(claim)
    await db.flush()
    result = await db.execute(
        select(ExpenseClaim)
        .options(selectinload(ExpenseClaim.user))
        .where(ExpenseClaim.id == claim.id)
    )
    return _expense_response(result.scalars().one())


@router.get("/expenses", response_model=List[ExpenseClaimResponse])
async def list_expenses(
    scope: str = Query("mine"),
    status_filter: Optional[str] = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    q = select(ExpenseClaim).options(selectinload(ExpenseClaim.user))
    if scope == "mine":
        q = q.where(ExpenseClaim.user_id == current_user.id)
    elif scope == "team":
        visible = await _visible_staff_ids(db, current_user)
        if visible is not None:
            q = q.where(ExpenseClaim.user_id.in_(visible))
    elif scope == "all":
        if not is_privileged(current_user.role):
            raise HTTPException(status_code=403, detail="Not allowed")
    else:
        raise HTTPException(status_code=400, detail="Invalid scope")
    if status_filter:
        q = q.where(ExpenseClaim.status == status_filter)
    q = q.order_by(ExpenseClaim.created_at.desc())
    result = await db.execute(q)
    return [_expense_response(r) for r in result.scalars().all()]


@router.post("/expenses/{expense_id}/{decision}", response_model=ExpenseClaimResponse)
async def decide_expense(
    expense_id: str,
    decision: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    """Managers approve/reject; founder/admin can also mark paid via decision=paid."""
    allowed = DECISION_STATUSES | {"paid"}
    if decision not in allowed:
        raise HTTPException(status_code=400, detail="Invalid decision")
    result = await db.execute(
        select(ExpenseClaim)
        .options(selectinload(ExpenseClaim.user))
        .where(ExpenseClaim.id == expense_id)
    )
    claim = result.scalars().first()
    if not claim:
        raise HTTPException(status_code=404, detail="Expense not found")

    if decision == "paid":
        if not is_privileged(current_user.role):
            raise HTTPException(status_code=403, detail="Only founder or admin can mark paid")
        if claim.status not in ("approved", "pending"):
            raise HTTPException(status_code=400, detail="Expense must be approved first")
        claim.status = "paid"
        claim.paid_at = datetime.now(timezone.utc)
        if claim.decided_by_user_id is None:
            claim.decided_by_user_id = current_user.id
            claim.decided_at = datetime.now(timezone.utc)
    else:
        if claim.status != "pending":
            raise HTTPException(status_code=400, detail="Already decided")
        if current_user.id == claim.user_id and not is_privileged(current_user.role):
            raise HTTPException(status_code=403, detail="Cannot approve your own expense")
        if not await _can_manage_user(db, current_user, claim.user_id):
            raise HTTPException(status_code=403, detail="Not allowed")
        claim.status = decision
        claim.decided_by_user_id = current_user.id
        claim.decided_at = datetime.now(timezone.utc)

    await db.flush()
    result = await db.execute(
        select(ExpenseClaim)
        .options(selectinload(ExpenseClaim.user))
        .where(ExpenseClaim.id == claim.id)
    )
    return _expense_response(result.scalars().one())


@router.get("/expenses/{expense_id}/receipt")
async def download_expense_receipt(
    expense_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    result = await db.execute(select(ExpenseClaim).where(ExpenseClaim.id == expense_id))
    claim = result.scalars().first()
    if not claim or not claim.receipt_object_key:
        raise HTTPException(status_code=404, detail="Receipt not found")
    if not await _can_manage_user(db, current_user, claim.user_id):
        raise HTTPException(status_code=403, detail="Not allowed")
    data = read_object(claim.receipt_object_key)
    filename = claim.receipt_original_filename or "receipt"
    return Response(
        content=data,
        media_type=claim.receipt_content_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ----------------- documents -----------------


@router.post("/documents", response_model=StaffDocumentResponse, status_code=201)
async def upload_staff_document(
    user_id: str = Form(...),
    title: str = Form(...),
    kind: str = Form("other"),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    if kind not in DOC_KINDS:
        raise HTTPException(status_code=400, detail="Invalid document kind")
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalars().first()
    if not target or target.role == "client":
        raise HTTPException(status_code=404, detail="Staff member not found")
    stored, original, content_type, size = await save_staff_upload(user_id, file)
    doc = StaffDocument(
        user_id=user_id,
        title=title.strip() or original,
        kind=kind,
        original_filename=original,
        stored_filename=stored,
        content_type=content_type,
        size_bytes=size,
        uploaded_by=current_user.id,
    )
    db.add(doc)
    await db.flush()
    await db.refresh(doc)
    return StaffDocumentResponse(
        id=doc.id,
        user_id=doc.user_id,
        title=doc.title,
        kind=doc.kind,
        original_filename=doc.original_filename,
        content_type=doc.content_type,
        size_bytes=doc.size_bytes,
        uploaded_by=doc.uploaded_by,
        created_at=doc.created_at,
    )


@router.get("/documents", response_model=List[StaffDocumentResponse])
async def list_documents(
    user_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    target_id = user_id or current_user.id
    if target_id != current_user.id and not is_privileged(current_user.role):
        raise HTTPException(status_code=403, detail="Not allowed")
    result = await db.execute(
        select(StaffDocument)
        .where(StaffDocument.user_id == target_id)
        .order_by(StaffDocument.created_at.desc())
    )
    return [
        StaffDocumentResponse(
            id=d.id,
            user_id=d.user_id,
            title=d.title,
            kind=d.kind,
            original_filename=d.original_filename,
            content_type=d.content_type,
            size_bytes=d.size_bytes,
            uploaded_by=d.uploaded_by,
            created_at=d.created_at,
        )
        for d in result.scalars().all()
    ]


@router.get("/documents/{doc_id}/download")
async def download_document(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    result = await db.execute(select(StaffDocument).where(StaffDocument.id == doc_id))
    doc = result.scalars().first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.user_id != current_user.id and not is_privileged(current_user.role):
        raise HTTPException(status_code=403, detail="Not allowed")
    data = read_staff_document(doc.user_id, doc.stored_filename)
    return Response(
        content=data,
        media_type=doc.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{doc.original_filename}"'},
    )


# ----------------- announcements -----------------


@router.get("/announcements", response_model=List[AnnouncementResponse])
async def list_announcements(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    result = await db.execute(
        select(Announcement)
        .options(selectinload(Announcement.created_by))
        .order_by(Announcement.created_at.desc())
        .limit(limit)
    )
    return [_announcement_response(a) for a in result.scalars().all()]


@router.post("/announcements", response_model=AnnouncementResponse, status_code=201)
async def create_announcement(
    payload: AnnouncementCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    ann = Announcement(
        title=payload.title.strip(),
        body=payload.body.strip(),
        created_by_user_id=current_user.id,
    )
    db.add(ann)
    await db.flush()
    result = await db.execute(
        select(Announcement)
        .options(selectinload(Announcement.created_by))
        .where(Announcement.id == ann.id)
    )
    return _announcement_response(result.scalars().one())


# ----------------- payslips -----------------


@router.get("/payslips/preview")
async def preview_payslip(
    user_id: str = Query(...),
    month_key: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_founder_ceo),
):
    """Preview unpaid leave hours and suggested deduction for a month."""
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalars().first()
    if not target or target.role == "client":
        raise HTTPException(status_code=404, detail="Staff member not found")
    try:
        unpaid_h = await _unpaid_hours_for_month(db, user_id, month_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="month_key must be YYYY-MM") from exc
    hourly = resolve_hourly_rate_paise(
        hourly_rate_paise=target.hourly_rate_paise,
        monthly_gross_paise=target.monthly_gross_paise,
    )
    unpaid_ded = unpaid_deduction_paise(unpaid_h, hourly)
    settings_row = (
        await db.execute(select(Settings).where(Settings.id == "default"))
    ).scalars().first()
    from ..services.tax_payroll import statutory_deductions_paise
    gross = target.monthly_gross_paise or 0
    statutory_total, statutory_breakdown = statutory_deductions_paise(settings_row, gross)
    return {
        "user_id": user_id,
        "month_key": month_key,
        "monthly_gross_paise": gross,
        "hourly_rate_paise": hourly,
        "hours_per_month": HOURS_PER_MONTH,
        "unpaid_hours": unpaid_h,
        "unpaid_deduction_paise": unpaid_ded,
        "statutory_deduction_paise": statutory_total,
        "statutory_breakdown": statutory_breakdown,
        "suggested_deductions_paise": unpaid_ded + statutory_total,
    }


@router.post("/payslips", response_model=PayslipResponse, status_code=201)
async def publish_payslip(
    payload: PayslipPublishRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_founder_ceo),
):
    result = await db.execute(select(User).where(User.id == payload.user_id))
    target = result.scalars().first()
    if not target or target.role == "client":
        raise HTTPException(status_code=404, detail="Staff member not found")
    gross = payload.gross_paise if payload.gross_paise is not None else (target.monthly_gross_paise or 0)
    if gross <= 0:
        raise HTTPException(status_code=400, detail="Set monthly salary or provide gross_paise")

    try:
        unpaid_h = await _unpaid_hours_for_month(db, payload.user_id, payload.month_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="month_key must be YYYY-MM") from exc
    hourly = resolve_hourly_rate_paise(
        hourly_rate_paise=target.hourly_rate_paise,
        monthly_gross_paise=target.monthly_gross_paise or gross,
    )
    unpaid_ded = unpaid_deduction_paise(unpaid_h, hourly)
    settings_row = (
        await db.execute(select(Settings).where(Settings.id == "default"))
    ).scalars().first()
    from ..services.tax_payroll import statutory_deductions_paise
    statutory_total, statutory_breakdown = statutory_deductions_paise(settings_row, gross)
    extra = max(0, payload.deductions_paise or 0)
    deductions = unpaid_ded + statutory_total + extra
    net = max(0, gross - deductions)

    existing = await db.execute(
        select(Payslip).where(
            Payslip.user_id == payload.user_id,
            Payslip.month_key == payload.month_key,
        )
    )
    if existing.scalars().first():
        raise HTTPException(status_code=400, detail="Payslip already published for this month")

    company = settings_row.company_name if settings_row else "KR Web Solution"

    note_parts = []
    if payload.note:
        note_parts.append(payload.note.strip())
    if unpaid_h > 0:
        note_parts.append(
            f"Unpaid leave: {unpaid_h:g}h × ₹{hourly / 100:,.2f}/hr = ₹{unpaid_ded / 100:,.2f}"
        )
    if statutory_breakdown.get("pf"):
        note_parts.append(f"PF: ₹{statutory_breakdown['pf'] / 100:,.2f}")
    if statutory_breakdown.get("esi"):
        note_parts.append(f"ESI: ₹{statutory_breakdown['esi'] / 100:,.2f}")
    if statutory_breakdown.get("pt"):
        note_parts.append(f"PT: ₹{statutory_breakdown['pt'] / 100:,.2f}")
    if extra > 0:
        note_parts.append(f"Extra deductions: ₹{extra / 100:,.2f}")
    note = " | ".join(note_parts) or None

    pdf_bytes = generate_payslip_pdf(
        employee_name=target.full_name,
        employee_email=target.email,
        job_title=target.job_title,
        department=target.department,
        month_key=payload.month_key,
        gross_paise=gross,
        deductions_paise=deductions,
        net_paise=net,
        note=note,
        company_name=company,
    )
    key = save_payslip_pdf(target.id, payload.month_key, pdf_bytes)

    slip = Payslip(
        user_id=target.id,
        month_key=payload.month_key,
        gross_paise=gross,
        deductions_paise=deductions,
        net_paise=net,
        note=note,
        pdf_object_key=key,
        published_by_user_id=current_user.id,
    )
    db.add(slip)
    await db.flush()
    result = await db.execute(
        select(Payslip).options(selectinload(Payslip.user)).where(Payslip.id == slip.id)
    )
    return _payslip_response(result.scalars().one())


@router.get("/payslips", response_model=List[PayslipResponse])
async def list_payslips(
    user_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    target_id = user_id or current_user.id
    if target_id != current_user.id and not is_privileged(current_user.role):
        raise HTTPException(status_code=403, detail="Workers only see their own payslips")
    result = await db.execute(
        select(Payslip)
        .options(selectinload(Payslip.user))
        .where(Payslip.user_id == target_id)
        .order_by(Payslip.month_key.desc())
    )
    return [_payslip_response(p) for p in result.scalars().all()]


@router.get("/payslips/{payslip_id}/pdf")
async def download_payslip_pdf(
    payslip_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    result = await db.execute(select(Payslip).where(Payslip.id == payslip_id))
    slip = result.scalars().first()
    if not slip or not slip.pdf_object_key:
        raise HTTPException(status_code=404, detail="Payslip not found")
    if slip.user_id != current_user.id and not is_privileged(current_user.role):
        raise HTTPException(status_code=403, detail="Not allowed")
    data = read_object(slip.pdf_object_key)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="payslip-{slip.month_key}.pdf"'
        },
    )


# ----------------- inbox (approvals) -----------------


@router.get("/inbox", response_model=StaffInboxResponse)
async def staff_inbox(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_staff),
):
    if not await _is_manager(db, current_user):
        raise HTTPException(status_code=403, detail="Managers only")

    visible = await _visible_staff_ids(db, current_user)

    leave_q = select(LeaveRequest).options(selectinload(LeaveRequest.user)).where(
        LeaveRequest.status == "pending"
    )
    att_q = select(AttendanceDay).options(selectinload(AttendanceDay.user)).where(
        AttendanceDay.correction_status == "pending"
    )
    exp_q = select(ExpenseClaim).options(selectinload(ExpenseClaim.user)).where(
        ExpenseClaim.status == "pending"
    )
    if visible is not None:
        leave_q = leave_q.where(LeaveRequest.user_id.in_(visible))
        att_q = att_q.where(AttendanceDay.user_id.in_(visible))
        exp_q = exp_q.where(ExpenseClaim.user_id.in_(visible))

    leave_rows = (await db.execute(leave_q.order_by(LeaveRequest.created_at.asc()))).scalars().all()
    att_rows = (await db.execute(att_q.order_by(AttendanceDay.work_date.desc()))).scalars().all()
    exp_rows = (await db.execute(exp_q.order_by(ExpenseClaim.created_at.asc()))).scalars().all()

    pending_bank: List[StaffProfileResponse] = []
    if is_privileged(current_user.role):
        bank_q = await db.execute(
            select(User).where(
                _staff_roles_filter(),
                or_(
                    User.pending_bank_account.isnot(None),
                    User.pending_bank_ifsc.isnot(None),
                ),
            )
        )
        for u in bank_q.scalars().all():
            pending_bank.append(await _profile_response(db, u))

    return StaffInboxResponse(
        pending_leave=[_leave_response(r) for r in leave_rows],
        pending_corrections=[_attendance_response(r) for r in att_rows],
        pending_expenses=[_expense_response(r) for r in exp_rows],
        pending_bank_changes=pending_bank,
    )
