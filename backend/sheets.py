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
