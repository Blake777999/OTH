# Old Town Hours — Scheduling System

A full-stack scheduling website and mathematical optimization engine designed to schedule staff across two houses (**Burn** and **THC**) from **9:00 AM to 9:00 PM every day (7 days/week)**.

---

## Key Features

1. **Dual House Coverage**: Guarantees exactly 1 person at **Burn** and 1 person at **THC** for every 30-minute slot from 9:00 AM to 9:00 PM (336 total half-hour slots = 168 hours/week).
2. **Fair Share & Equal Hours**:
   - For 12 active members: exactly **14.0 hours** per member per week.
   - Expandable to 13 members: balances seamlessly around **~13.0 hours** (25–26 slots) each.
   - Jared is pre-configured as the 13th member with a 1-click **Activate** toggle when he joins!
3. **Master vs. Weekly Exceptions**:
   - **Master Schedule**: Interactive drag-and-drop calendar painter where each member submits recurring weekly commitments (classes, labs, club meetings).
   - **Weekly Exceptions**: Visual painter for one-off commitments (exams, travel, appointments) for a specific week.
4. **Repeat-Week Consistency**:
   - The CP-SAT optimizer prioritizes giving people the **same times every week**, even if houses are swapped between Burn and THC!
5. **Shift Style Preferences**:
   - Members can choose between:
     - **~2 Hours Everyday** (frequent, shorter shifts spread across the week).
     - **Fewer Days, Longer Shifts** (e.g. 3–5 hours across 3–4 days).
   - Enforces minimum 1.0-hour shift lengths (no isolated 30-minute shifts) and discourages jumping between houses mid-shift.
6. **Google Sheets Export & Synchronization**:
   - **1-Click Formatted Copy**: Click *"Copy for Google Sheets"*, open any Google Sheet, click cell A1, and press Ctrl+V (or Cmd+V) to get the complete formatted 7-day schedule with Burn & THC columns and time slots.
   - **Direct Google API Sync**: Connect your Google Spreadsheet ID and Service Account JSON key to automatically push the weekly schedule and member hours breakdown directly to Google Sheets on every run!
   - **CSV Export**: Direct downloadable CSV format.

---

## Group Roster (Pre-configured)

1. Dylan
2. Nate
3. Simon
4. Blake Cohen
5. Jacob
6. Oliver
7. Kobi
8. Ethan
9. Sebastian
10. Corey
11. Alex
12. Blake Rosen
13. Jared *(eventual 13th member — preloaded and ready to activate)*

---

## Quick Start

### 1. Launch the Server

Run the start script:
```bash
./run.sh
```

Or run via Uvicorn directly:
```bash
./venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

### 2. Open the Website

Navigate to:
- **Web App**: [http://localhost:8000](http://localhost:8000)
- **Interactive API Documentation**: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## Running the Automated Test Suite

```bash
PYTHONPATH=. ./venv/bin/pytest tests/
```
All unit tests verify:
- Complete coverage of all 336 slots (Burn & THC, 9am–9pm daily).
- Equal hours fairness (14h for 12 members, ~13h for 13 members).
- Master schedule & weekly override compliance.
- High repeat-week time consistency.
- CSV/TSV format and API endpoints.
