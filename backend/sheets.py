"""
Google Sheets Synchronization and CSV/TSV Export Service.
Provides live Google Sheets API syncing via gspread, as well as zero-config
one-click copy-paste TSV formatting and CSV downloads.
"""

import io
import csv
import json
import logging
from typing import List, Dict, Optional, Any
from datetime import datetime
from backend.models import (
    Member,
    ScheduleSlotAssignment,
    DAYS_OF_WEEK,
    HOUSES,
    SLOTS_PER_DAY,
    START_HOUR,
    slot_to_time_str,
    slot_range_to_str,
)

logger = logging.getLogger("sheets")

def build_schedule_grid_matrix(
    assignments: List[ScheduleSlotAssignment],
    members_map: Dict[str, Member]
) -> List[List[str]]:
    """
    Constructs a 2D matrix representing the weekly schedule:
    Row 0: Header with Day names merged or paired
    Row 1: Subheaders: Time | Burn | THC | Burn | THC ...
    Rows 2..25: Time slots (9:00 AM - 9:30 AM, etc.) with assigned member names
    """
    header_days = ["Time Range"]
    for day in DAYS_OF_WEEK:
        header_days.extend([f"{day} (Burn)", f"{day} (THC)"])

    # Lookup map: (day_of_week, slot, house) -> member_name
    grid_data = {}
    for a in assignments:
        grid_data[(a.day_of_week, a.slot, a.house)] = a.member_name or members_map.get(a.member_id, Member(name="Unknown")).name

    rows = [header_days]
    for s in range(SLOTS_PER_DAY):
        time_label = slot_range_to_str(s, s + 1)
        row = [time_label]
        for d in range(len(DAYS_OF_WEEK)):
            burn_person = grid_data.get((d, s, "Burn"), "—")
            thc_person = grid_data.get((d, s, "THC"), "—")
            row.extend([burn_person, thc_person])
        rows.append(row)

    return rows

def generate_csv(
    week_id: str,
    assignments: List[ScheduleSlotAssignment],
    members: List[Member]
) -> str:
    """Generates standard CSV content for the schedule."""
    members_map = {m.id: m for m in members}
    matrix = build_schedule_grid_matrix(assignments, members_map)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([f"Old Town Hours - Week {week_id}"])
    writer.writerow([])
    for row in matrix:
        writer.writerow(row)
    return output.getvalue()

def generate_tsv_for_sheets(
    week_id: str,
    assignments: List[ScheduleSlotAssignment],
    members: List[Member]
) -> str:
    """Generates tab-separated text ready for immediate 1-click clipboard paste into Google Sheets."""
    members_map = {m.id: m for m in members}
    matrix = build_schedule_grid_matrix(assignments, members_map)
    output = io.StringIO()
    writer = csv.writer(output, delimiter="\t")
    for row in matrix:
        writer.writerow(row)
    return output.getvalue()

def generate_excel_for_sheets(
    week_id: str,
    assignments: List[ScheduleSlotAssignment],
    members: List[Member],
    stats: Optional[Dict[str, Any]] = None,
) -> bytes:
    """
    Generates a beautifully formatted Excel workbook (.xlsx) designed specifically
    to be imported directly into Google Sheets (File > Import > Upload).
    Contains 2 sheets:
      1. 'Weekly Schedule' - visual grid with Burn and THC columns for all 7 days
      2. 'Hours Summary' - member breakdown of assigned hours, targets, and shifts
    """
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    # Sheet 1: Schedule
    ws_sched = wb.active
    ws_sched.title = "Weekly Schedule"
    ws_sched.views.sheetView[0].showGridLines = True

    # Styles
    title_font = Font(name="Calibri", size=14, bold=True, color="1E3A8A")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    day_header_fill = PatternFill(start_color="1E40AF", end_color="1E40AF", fill_type="solid")
    burn_header_fill = PatternFill(start_color="DC2626", end_color="DC2626", fill_type="solid")
    thc_header_fill = PatternFill(start_color="16A34A", end_color="16A34A", fill_type="solid")
    time_header_fill = PatternFill(start_color="334155", end_color="334155", fill_type="solid")
    zebra_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")

    thin_border_side = Side(border_style="thin", color="CBD5E1")
    cell_border = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)

    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")

    # Title row
    ws_sched.merge_cells("A1:O1")
    ws_sched["A1"] = f"Old Town Hours — Weekly Schedule: {week_id}"
    ws_sched["A1"].font = title_font
    ws_sched["A1"].alignment = Alignment(horizontal="left", vertical="center")
    ws_sched.row_dimensions[1].height = 28

    # Row 2: Days Header (Merged across Burn and THC for each day)
    ws_sched["A2"] = "Time"
    ws_sched["A2"].font = header_font
    ws_sched["A2"].fill = time_header_fill
    ws_sched["A2"].alignment = align_center
    ws_sched["A2"].border = cell_border

    col_idx = 2
    for d, day_name in enumerate(DAYS_OF_WEEK):
        c1 = get_column_letter(col_idx)
        c2 = get_column_letter(col_idx + 1)
        ws_sched.merge_cells(f"{c1}2:{c2}2")
        ws_sched[f"{c1}2"] = day_name
        ws_sched[f"{c1}2"].font = header_font
        ws_sched[f"{c1}2"].fill = day_header_fill
        ws_sched[f"{c1}2"].alignment = align_center
        col_idx += 2
    ws_sched.row_dimensions[2].height = 22

    # Row 3: Subheader: Time Range | Burn | THC | Burn | THC ...
    ws_sched["A3"] = "9:00 AM - 7:00 PM"
    ws_sched["A3"].font = Font(name="Calibri", size=9, italic=True, color="64748B")
    ws_sched["A3"].alignment = align_center
    ws_sched["A3"].border = cell_border

    col_idx = 2
    for d in range(len(DAYS_OF_WEEK)):
        c_burn = ws_sched.cell(row=3, column=col_idx, value="Burn")
        c_burn.font = header_font
        c_burn.fill = burn_header_fill
        c_burn.alignment = align_center
        c_burn.border = cell_border

        c_thc = ws_sched.cell(row=3, column=col_idx + 1, value="THC")
        c_thc.font = header_font
        c_thc.fill = thc_header_fill
        c_thc.alignment = align_center
        c_thc.border = cell_border
        col_idx += 2
    ws_sched.row_dimensions[3].height = 20

    # Populate 24 slots (Rows 4..27)
    members_map = {m.id: m for m in members}
    grid_data = {}
    for a in assignments:
        mname = a.member_name or members_map.get(a.member_id, Member(name="Unknown")).name
        grid_data[(a.day_of_week, a.slot, a.house)] = mname

    for s in range(SLOTS_PER_DAY):
        row_num = 4 + s
        ws_sched.row_dimensions[row_num].height = 20
        time_str = slot_range_to_str(s, s + 1)
        c_time = ws_sched.cell(row=row_num, column=1, value=time_str)
        c_time.font = Font(name="Calibri", size=10, bold=True)
        c_time.alignment = align_center
        c_time.border = cell_border

        col_idx = 2
        for d in range(len(DAYS_OF_WEEK)):
            burn_val = grid_data.get((d, s, "Burn"), "—")
            thc_val = grid_data.get((d, s, "THC"), "—")

            cb = ws_sched.cell(row=row_num, column=col_idx, value=burn_val)
            cb.alignment = align_center
            cb.border = cell_border
            cb.font = Font(name="Calibri", size=10)
            if burn_val == "UNFILLED" or burn_val.startswith("❌"):
                cb.fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
                cb.font = Font(name="Calibri", size=10, bold=True, color="DC2626")
            elif s % 2 == 1:
                cb.fill = zebra_fill

            ct = ws_sched.cell(row=row_num, column=col_idx + 1, value=thc_val)
            ct.alignment = align_center
            ct.border = cell_border
            ct.font = Font(name="Calibri", size=10)
            if thc_val == "UNFILLED" or thc_val.startswith("❌"):
                ct.fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
                ct.font = Font(name="Calibri", size=10, bold=True, color="DC2626")
            elif s % 2 == 1:
                ct.fill = zebra_fill

            col_idx += 2

    # Auto-adjust column widths for Schedule tab
    for col in ws_sched.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val_str = str(cell.value or "")
            if cell.row > 1 and len(val_str) > max_len:
                max_len = len(val_str)
        ws_sched.column_dimensions[col_letter].width = max(max_len + 3, 14)
    ws_sched.column_dimensions["A"].width = 22

    # Sheet 2: Hours Summary
    ws_sum = wb.create_sheet(title="Hours Summary")
    ws_sum.views.sheetView[0].showGridLines = True

    # Title
    ws_sum.merge_cells("A1:H1")
    ws_sum["A1"] = f"Old Town Hours — Member Hours Summary: {week_id}"
    ws_sum["A1"].font = title_font
    ws_sum.row_dimensions[1].height = 28

    headers = [
        "Member",
        "Assigned Hours",
        "Target Hours",
        "Difference",
        "🔥 Burn Hours",
        "🌿 THC Hours",
        "Days Worked",
        "Total Shifts",
    ]
    for c_idx, h_text in enumerate(headers, start=1):
        cell = ws_sum.cell(row=2, column=c_idx, value=h_text)
        cell.font = header_font
        cell.fill = time_header_fill
        cell.alignment = align_center
        cell.border = cell_border
    ws_sum.row_dimensions[2].height = 22

    # Calculate summary rows
    active_members = [m for m in members if m.active]
    target_hours = round(140.0 / max(len(active_members), 1), 1)

    member_stats = {}
    for m in active_members:
        member_stats[m.id] = {
            "name": m.name,
            "burn_slots": 0,
            "thc_slots": 0,
            "days_set": set(),
            "shifts_count": 0,
        }

    for d in range(7):
        for house in ["Burn", "THC"]:
            prev_m = None
            for s in range(SLOTS_PER_DAY):
                a = next((x for x in assignments if x.day_of_week == d and x.slot == s and x.house == house), None)
                cur_m = a.member_id if a and a.member_id != "UNFILLED" else None
                if cur_m and cur_m in member_stats:
                    if house == "Burn":
                        member_stats[cur_m]["burn_slots"] += 1
                    else:
                        member_stats[cur_m]["thc_slots"] += 1
                    member_stats[cur_m]["days_set"].add(d)

                if cur_m and cur_m != prev_m:
                    if cur_m in member_stats:
                        member_stats[cur_m]["shifts_count"] += 1
                prev_m = cur_m

    sorted_members = sorted(member_stats.values(), key=lambda x: x["name"])
    for idx, ms in enumerate(sorted_members, start=3):
        ws_sum.row_dimensions[idx].height = 20
        burn_h = ms["burn_slots"] * 0.5
        thc_h = ms["thc_slots"] * 0.5
        total_h = burn_h + thc_h
        diff_h = round(total_h - target_hours, 1)

        row_vals = [
            ms["name"],
            f"{total_h:.1f}",
            f"{target_hours:.1f}",
            f"{'+' if diff_h > 0 else ''}{diff_h:.1f}",
            f"{burn_h:.1f}",
            f"{thc_h:.1f}",
            len(ms["days_set"]),
            ms["shifts_count"],
        ]
        for c_idx, val in enumerate(row_vals, start=1):
            cell = ws_sum.cell(row=idx, column=c_idx, value=val)
            cell.border = cell_border
            cell.alignment = align_left if c_idx == 1 else align_center
            cell.font = Font(name="Calibri", size=10)
            if idx % 2 == 0:
                cell.fill = zebra_fill

    # Auto-adjust column widths for Summary tab
    for col in ws_sum.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val_str = str(cell.value or "")
            if cell.row > 1 and len(val_str) > max_len:
                max_len = len(val_str)
        ws_sum.column_dimensions[col_letter].width = max(max_len + 4, 15)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

def sync_to_google_sheet(
    spreadsheet_id: str,
    service_account_json_str: str,
    week_id: str,
    assignments: List[ScheduleSlotAssignment],
    members: List[Member],
    stats: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Syncs the generated schedule directly into Google Sheets using gspread.
    Creates or updates two worksheets:
    1. 'Week {week_id}' -> Visual 7-day schedule grid with Burn & THC
    2. 'Hours Summary - {week_id}' -> Member fairness and total hours breakdown
    """
    if not spreadsheet_id or not spreadsheet_id.strip():
        return {
            "success": False,
            "message": "Spreadsheet ID is missing. Please enter your Google Spreadsheet ID.",
        }

    if not service_account_json_str or not service_account_json_str.strip():
        return {
            "success": False,
            "message": "Google Service Account credentials are not configured.",
        }

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        # Parse credentials
        creds_dict = json.loads(service_account_json_str.strip())
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(credentials)

        spreadsheet = client.open_by_key(spreadsheet_id.strip())
        
        # 1. Update Schedule Worksheet
        sheet_title = f"Schedule {week_id}"
        try:
            worksheet = spreadsheet.worksheet(sheet_title)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=sheet_title, rows=40, cols=20)

        members_map = {m.id: m for m in members}
        matrix = build_schedule_grid_matrix(assignments, members_map)
        
        # Prepare batch update values
        worksheet.clear()
        worksheet.update(values=matrix, range_name="A1")
        
        # Format headers with bold style
        try:
            worksheet.format("A1:O1", {
                "textFormat": {"bold": True, "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}},
                "backgroundColor": {"red": 0.12, "green": 0.23, "blue": 0.36}, # Dark Navy
                "horizontalAlignment": "CENTER",
            })
            worksheet.format("A2:A26", {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.95, "green": 0.95, "blue": 0.95},
                "horizontalAlignment": "CENTER",
            })
        except Exception as e:
            logger.warning(f"Could not apply sheet cell styling: {e}")

        # 2. Update Hours Summary Worksheet
        summary_title = f"Summary {week_id}"
        try:
            summary_ws = spreadsheet.worksheet(summary_title)
        except gspread.WorksheetNotFound:
            summary_ws = spreadsheet.add_worksheet(title=summary_title, rows=30, cols=10)

        summary_ws.clear()
        summary_rows = [
            ["Member Name", "Assigned Hours", "Target Hours", "Diff (Hrs)", "Burn Hours", "THC Hours", "Days Worked", "Shift Preference"]
        ]
        if stats and "members" in stats:
            for m_stat in stats["members"]:
                summary_rows.append([
                    m_stat["name"],
                    m_stat["assigned_hours"],
                    m_stat["target_hours"],
                    m_stat["diff_hours"],
                    m_stat["burn_hours"],
                    m_stat["thc_hours"],
                    m_stat["days_worked"],
                    "2h Everyday" if m_stat["preference"] == "daily_short" else "Fewer Days (Longer)",
                ])
        summary_ws.update(values=summary_rows, range_name="A1")

        sheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
        return {
            "success": True,
            "message": f"Successfully synced week {week_id} to Google Sheets!",
            "sheet_url": sheet_url,
            "synced_at": datetime.now().isoformat(),
        }

    except json.JSONDecodeError:
        return {
            "success": False,
            "message": "Invalid Service Account JSON format. Please paste valid JSON credentials.",
        }
    except Exception as e:
        logger.error(f"Error syncing to Google Sheet: {e}", exc_info=True)
        return {
            "success": False,
            "message": f"Google Sheets Sync Error: {str(e)}",
        }
