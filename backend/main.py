import os
import json
from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import datetime, date, timedelta

from fastapi import FastAPI, HTTPException, Query, Response, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.models import (
    init_db,
    get_all_members,
    get_member,
    create_member,
    update_member,
    delete_member,
    get_master_schedule,
    save_master_schedule_for_member,
    get_weekly_overrides,
    save_weekly_overrides_for_member,
    save_schedule,
    get_schedule,
    get_all_scheduled_weeks,
    get_config,
    set_config,
    Member,
    MasterScheduleItem,
    WeeklyOverrideItem,
    ScheduleSlotAssignment,
    GoogleSheetConfig,
    DAYS_OF_WEEK,
    HOUSES,
    SLOTS_PER_DAY,
    START_HOUR,
    slot_to_time_str,
    slot_range_to_str,
)
from backend.scheduler import ScheduleOptimizer
from backend.sheets import (
    generate_csv,
    generate_tsv_for_sheets,
    generate_excel_for_sheets,
    sync_to_google_sheet,
)

# Initialize DB
init_db()

app = FastAPI(title="Old Town Hours Scheduler API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# Helper for ISO week math
def get_current_week_id() -> str:
    today = date.today()
    year, week, _ = today.isocalendar()
    return f"{year}-W{week:02d}"

def get_previous_week_id(week_id: str) -> str:
    try:
        parts = week_id.split("-W")
        year = int(parts[0])
        week = int(parts[1])
        # Find Monday of this week
        monday = date.fromisocalendar(year, week, 1)
        prev_monday = monday - timedelta(days=7)
        py, pw, _ = prev_monday.isocalendar()
        return f"{py}-W{pw:02d}"
    except Exception:
        return ""

def get_week_dates(week_id: str) -> List[str]:
    """Returns formatted date strings for Monday..Sunday of the given week_id."""
    try:
        parts = week_id.split("-W")
        year = int(parts[0])
        week = int(parts[1])
        monday = date.fromisocalendar(year, week, 1)
        dates = []
        for i in range(7):
            d = monday + timedelta(days=i)
            dates.append(d.strftime("%b %d"))
        return dates
    except Exception:
        return [f"Day {i+1}" for i in range(7)]

# ----------------- Member Endpoints -----------------

@app.get("/api/members", response_model=List[Member])
def api_get_members(only_active: bool = False):
    return get_all_members(only_active=only_active)

@app.post("/api/members", response_model=Member)
def api_create_member(member: Member):
    return create_member(member)

@app.put("/api/members/{member_id}", response_model=Member)
def api_update_member(member_id: str, member: Member):
    member.id = member_id
    existing = get_member(member_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Member not found")
    return update_member(member)

@app.delete("/api/members/{member_id}")
def api_delete_member(member_id: str):
    existing = get_member(member_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Member not found")
    delete_member(member_id)
    return {"success": True, "message": "Member deleted"}

# ----------------- Master Schedule Endpoints -----------------

@app.get("/api/master-schedule", response_model=List[MasterScheduleItem])
def api_get_all_master_schedule():
    return get_master_schedule()

@app.get("/api/members/{member_id}/master-schedule", response_model=List[MasterScheduleItem])
def api_get_member_master_schedule(member_id: str):
    return get_master_schedule(member_id=member_id)

@app.post("/api/members/{member_id}/master-schedule")
def api_save_member_master_schedule(member_id: str, items: List[MasterScheduleItem]):
    save_master_schedule_for_member(member_id, items)
    return {"success": True, "count": len(items)}

# ----------------- Weekly Override Endpoints -----------------

@app.get("/api/weekly-overrides", response_model=List[WeeklyOverrideItem])
def api_get_all_weekly_overrides(week_id: str = Query(...)):
    return get_weekly_overrides(week_id=week_id)

@app.get("/api/members/{member_id}/weekly-overrides", response_model=List[WeeklyOverrideItem])
def api_get_member_weekly_overrides(member_id: str, week_id: str = Query(...)):
    return get_weekly_overrides(week_id=week_id, member_id=member_id)

@app.post("/api/members/{member_id}/weekly-overrides")
def api_save_member_weekly_overrides(member_id: str, week_id: str = Query(...), items: List[WeeklyOverrideItem] = Body(...)):
    save_weekly_overrides_for_member(week_id, member_id, items)
    return {"success": True, "count": len(items)}

# ----------------- Schedule Generation & Retrieval -----------------

class GenerateScheduleRequest(BaseModel):
    week_id: str
    use_previous_week_consistency: bool = True
    reference_week_id: Optional[str] = None
    min_shift_hours: float = 1.0 # default 1.0 hour (2 slots)

@app.post("/api/schedules/generate")
def api_generate_schedule(req: GenerateScheduleRequest):
    week_id = req.week_id.strip()
    active_members = get_all_members(only_active=True)
    if not active_members:
        raise HTTPException(status_code=400, detail="No active members to schedule.")

    master_items = get_master_schedule()
    weekly_overrides = get_weekly_overrides(week_id=week_id)

    # Determine reference schedule for repeat consistency
    previous_schedule = []
    if req.use_previous_week_consistency:
        ref_week = req.reference_week_id or get_previous_week_id(week_id)
        if ref_week:
            previous_schedule = get_schedule(ref_week)
            # If previous week not found, check if any earlier schedule exists
            if not previous_schedule:
                all_weeks = get_all_scheduled_weeks()
                if all_weeks:
                    previous_schedule = get_schedule(all_weeks[0])

    min_shift_slots = max(1, int(req.min_shift_hours * 2))
    optimizer = ScheduleOptimizer(
        members=active_members,
        master_items=master_items,
        weekly_overrides=weekly_overrides,
        previous_schedule=previous_schedule,
        min_shift_slots=min_shift_slots,
    )

    result = optimizer.solve()
    if not result["success"]:
        return {
            "success": False,
            "status": result["status"],
            "message": "Optimization failed to find a valid schedule.",
            "warnings": result.get("warnings", []),
        }

    # Save to database
    save_schedule(week_id, result["assignments"])

    # Save stats in config for fast retrieval
    set_config(f"stats_{week_id}", json.dumps(result["stats"]))

    # Check auto sync to Google Sheets
    auto_sync = get_config("sheets_auto_sync", "false").lower() == "true"
    sheet_result = None
    if auto_sync:
        sheet_id = get_config("sheets_spreadsheet_id", "")
        sa_json = get_config("sheets_service_account_json", "")
        if sheet_id and sa_json:
            sheet_result = sync_to_google_sheet(
                spreadsheet_id=sheet_id,
                service_account_json_str=sa_json,
                week_id=week_id,
                assignments=result["assignments"],
                members=active_members,
                stats=result["stats"],
            )

    return {
        "success": True,
        "status": result["status"],
        "week_id": week_id,
        "assignments": result["assignments"],
        "stats": result["stats"],
        "warnings": result["warnings"],
        "google_sheets_sync": sheet_result,
    }

@app.get("/api/schedules/{week_id}")
def api_get_schedule(week_id: str):
    assignments = get_schedule(week_id)
    if not assignments:
        return {"found": False, "week_id": week_id, "assignments": [], "stats": None}

    stats_json = get_config(f"stats_{week_id}", "")
    stats = json.loads(stats_json) if stats_json else None
    
    # If stats not cached, compute summary
    if not stats:
        members = get_all_members(only_active=True)
        opt = ScheduleOptimizer(members, [], [])
        stats = opt._compute_stats(assignments, set(), {i: round(336/len(members)) for i in range(len(members))})

    dates = get_week_dates(week_id)

    return {
        "found": True,
        "week_id": week_id,
        "dates": dates,
        "assignments": assignments,
        "stats": stats,
    }

@app.put("/api/schedules/{week_id}/slots")
def api_update_schedule_slot(
    week_id: str,
    day_of_week: int = Body(...),
    slot: int = Body(...),
    house: str = Body(...),
    member_id: str = Body(...),
):
    """Allows manual override of an individual slot assignment."""
    member = get_member(member_id)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
        
    current = get_schedule(week_id)
    updated = []
    found = False
    for a in current:
        if a.day_of_week == day_of_week and a.slot == slot and a.house == house:
            updated.append(ScheduleSlotAssignment(
                day_of_week=day_of_week,
                slot=slot,
                house=house,
                member_id=member.id,
                member_name=member.name,
                color=member.color
            ))
            found = True
        else:
            updated.append(a)
            
    if not found:
        updated.append(ScheduleSlotAssignment(
            day_of_week=day_of_week,
            slot=slot,
            house=house,
            member_id=member.id,
            member_name=member.name,
            color=member.color
        ))

    save_schedule(week_id, updated)
    return {"success": True, "message": "Slot updated"}

@app.get("/api/schedules-weeks")
def api_get_all_weeks():
    weeks = get_all_scheduled_weeks()
    current = get_current_week_id()
    if current not in weeks:
        weeks.insert(0, current)
    return {"weeks": weeks, "current_week": current}

# ----------------- Exports & Google Sheets -----------------

@app.get("/api/schedules/{week_id}/export.xlsx")
def api_export_xlsx(week_id: str):
    assignments = get_schedule(week_id)
    if not assignments:
        raise HTTPException(status_code=404, detail="No schedule found for this week")
    members = get_all_members()
    stats_json = get_config(f"stats_{week_id}", "")
    stats = json.loads(stats_json) if stats_json else None
    xlsx_bytes = generate_excel_for_sheets(week_id, assignments, members, stats)
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="old_town_hours_{week_id}.xlsx"'}
    )

@app.get("/api/schedules/{week_id}/export.csv")
def api_export_csv(week_id: str):
    assignments = get_schedule(week_id)
    if not assignments:
        raise HTTPException(status_code=404, detail="No schedule found for this week")
    members = get_all_members()
    csv_str = generate_csv(week_id, assignments, members)
    return Response(
        content=csv_str,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="old_town_hours_{week_id}.csv"'}
    )

@app.get("/api/schedules/{week_id}/export.tsv")
def api_export_tsv(week_id: str):
    assignments = get_schedule(week_id)
    if not assignments:
        raise HTTPException(status_code=404, detail="No schedule found for this week")
    members = get_all_members()
    tsv_str = generate_tsv_for_sheets(week_id, assignments, members)
    return PlainTextResponse(content=tsv_str)

@app.get("/api/sheets/config")
def api_get_sheets_config():
    sheet_id = get_config("sheets_spreadsheet_id", "")
    auto_sync = get_config("sheets_auto_sync", "false").lower() == "true"
    sa_json = get_config("sheets_service_account_json", "")
    has_sa = bool(sa_json and sa_json.strip())
    last_synced = get_config("sheets_last_synced", "")

    return {
        "spreadsheet_id": sheet_id,
        "auto_sync": auto_sync,
        "has_credentials": has_sa,
        "last_synced": last_synced,
    }

@app.post("/api/sheets/config")
def api_save_sheets_config(config: GoogleSheetConfig):
    set_config("sheets_spreadsheet_id", config.spreadsheet_id.strip())
    set_config("sheets_auto_sync", "true" if config.auto_sync else "false")
    if config.service_account_json is not None and config.service_account_json.strip():
        set_config("sheets_service_account_json", config.service_account_json.strip())
    return {"success": True, "message": "Google Sheets settings saved."}

@app.post("/api/sheets/sync/{week_id}")
def api_sync_google_sheet(week_id: str):
    assignments = get_schedule(week_id)
    if not assignments:
        raise HTTPException(status_code=404, detail="No schedule generated for this week.")
        
    members = get_all_members(only_active=True)
    sheet_id = get_config("sheets_spreadsheet_id", "")
    sa_json = get_config("sheets_service_account_json", "")

    if not sheet_id:
        raise HTTPException(status_code=400, detail="Spreadsheet ID is not configured.")
    if not sa_json:
        raise HTTPException(status_code=400, detail="Service Account JSON credentials are not configured.")

    stats_json = get_config(f"stats_{week_id}", "")
    stats = json.loads(stats_json) if stats_json else None

    result = sync_to_google_sheet(
        spreadsheet_id=sheet_id,
        service_account_json_str=sa_json,
        week_id=week_id,
        assignments=assignments,
        members=members,
        stats=stats,
    )
    if result["success"]:
        set_config("sheets_last_synced", datetime.now().isoformat())
    return result

# ----------------- Static Frontend -----------------

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

@app.get("/")
def serve_index():
    index_file = FRONTEND_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "Old Town Hours API running. Frontend index.html not yet installed."}
