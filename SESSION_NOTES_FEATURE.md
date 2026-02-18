# Session Notes Feature

## What's New

The Braille Visualizer now includes an **in-app notes system** for capturing observations during touchscreen tuning sessions.

---

## Features

### 1. Add Notes (Manual)
- Click **"+ Add Note"** button in the sidebar
- Modal opens with:
  - **Observation** text area (required)
  - **Tags** field (freeform, comma-separated)
  - **Profile name** (auto-filled from current selection)
  - **Include register snapshot** checkbox (optional)

### 2. View Notes
- **Notes panel** in sidebar shows all notes chronologically (newest first)
- Each note displays:
  - Timestamp
  - Preview (first 80 characters)
  - Tags as colored chips
  - Profile name
  - 📋 icon if register snapshot included
- **Click to expand** — see full observation text + register snapshot JSON

### 3. Delete Notes
- Click **×** button on any note to delete

### 4. Export Notes
- Click **"Export Notes"** in Actions section
- Downloads markdown file: `touch_session_notes_YYYY-MM-DDTHH-MM-SS.md`
- Format:
  ```markdown
  # Touch Tuning Session Notes
  **Exported:** 2026-02-17 14:32:00
  **Total notes:** 5

  ## Note 1 — 2026-02-17 14:15:23
  **Profile:** Edge Suppression Active
  **Tags:** phantom, edge, improvement

  Phantom touches reduced significantly after enabling T42 edge suppression.
  Left edge phantom rate dropped from ~5/min to <1/min.

  **Register Snapshot:**
  ```json
  { "T100": { "TCHTHR": 40, ... }, ... }
  ```
  ```

---

## Backend API

### Endpoints
- `GET /api/notes` — List all notes
- `GET /api/notes/{note_id}` — Get single note
- `POST /api/notes` — Create note
- `DELETE /api/notes/{note_id}` — Delete note
- `GET /api/notes/export/markdown` — Export as markdown

### Storage
- **Directory:** `braille-visualizer/backend/session_notes/`
- **Format:** JSON files named `{timestamp}.json`
- **Schema:**
  ```json
  {
    "id": "20260217_143245_123456",
    "timestamp": "2026-02-17T14:32:45.123456",
    "observation": "Full text of observation...",
    "tags": ["phantom", "edge"],
    "profile_name": "Edge Suppression Active",
    "register_snapshot": { "T100": {...}, "T42": {...}, ... }
  }
  ```

---

## Usage Workflow

### Typical Session
1. **Start tuning** — apply baseline profile, test touch behavior
2. **Observe something interesting** — phantom touch, missed light touch, etc.
3. **Click "+ Add Note"** immediately while fresh
4. **Enter observation** — what you saw, where, when
5. **Add tags** — `phantom`, `edge`, `jitter`, `improvement`, etc.
6. **Include snapshot** (if you want to remember exact register values)
7. **Continue tuning** — apply new profile, test again, add more notes
8. **Export notes** at end of session → shareable markdown report

### Example Note Entries

**Note 1:**
- **Observation:** "Phantom touches at left edge (~x=0-50) with amp 8-12. Occurring every 10-15 seconds even with no hand near sensor."
- **Tags:** `phantom, edge, left`
- **Profile:** Factory Default
- **Snapshot:** Yes

**Note 2:**
- **Observation:** "After enabling T42.CTRL=3 (edge suppression), left edge phantoms dropped to <1/min. Right edge still clean. No impact on valid edge touches."
- **Tags:** `phantom, edge, improvement`
- **Profile:** Edge Suppression Active
- **Snapshot:** Yes

**Note 3:**
- **Observation:** "Light touches (amp 15-20) now reliably detected. No new phantoms introduced. Ready for A/B comparison with factory default."
- **Tags:** `sensitivity, testing`
- **Profile:** High Sensitivity + Edge Suppression
- **Snapshot:** No

---

## Design Decisions (from Discussion)

✅ **Manual note creation only** — No auto-prompts (simpler workflow)
✅ **Freeform tags** — Comma-separated text input (flexible, fast)
✅ **Register snapshot = current values** — Captures state at note creation time
✅ **Simple chronological list** — Newest first (easy to scan recent activity)
✅ **Basic markdown export** — Timestamps, observations, tags, snapshots

---

## File Locations

**Backend:**
- `backend/main.py` — Added notes endpoints (lines ~506-650)
- `backend/session_notes/` — JSON storage directory (auto-created)

**Frontend:**
- `frontend/index.html` — Added notes UI + modal + JavaScript functions
  - Notes panel in sidebar (~line 247)
  - Add Note modal (~line 310)
  - CSS styles (~line 175)
  - JavaScript functions (~line 1000)

---

## Future Enhancements (Not Implemented Yet)

These were discussed but deferred for v2:
- Search/filter notes by text content
- Edit existing notes
- Note templates (e.g., "Phantom touch report")
- Auto-prompt after profile apply
- Link notes to CSV recordings (when recording feature is added)
- Quick actions: "Apply this profile", "Compare to current"

---

## Testing Checklist

- [ ] Start visualizer backend (`Ctrl+Shift+V`)
- [ ] Open browser to `http://localhost:8001`
- [ ] Verify "Session Notes" panel appears in sidebar with count badge "0"
- [ ] Click "+ Add Note" → modal opens
- [ ] Profile field auto-filled with current selection
- [ ] Enter observation, add tags (comma-separated), check/uncheck snapshot
- [ ] Click "Save Note" → modal closes, note appears in list
- [ ] Note count badge updates (e.g., "1")
- [ ] Click note to expand → full text + snapshot (if included) shown
- [ ] Click × to delete → confirm dialog → note removed
- [ ] Add 2-3 more notes with different tags/profiles
- [ ] Click "Export Notes" in Actions → markdown file downloads
- [ ] Open markdown file → verify format, timestamps, tags, snapshots
- [ ] Refresh page → notes persist (loaded from backend storage)

---

## Troubleshooting

### Notes not appearing after refresh
- **Check:** Backend session_notes directory exists and contains .json files
- **Fix:** Look for errors in backend terminal (file write permissions?)

### Modal doesn't close after saving
- **Check:** Browser console for JavaScript errors
- **Fix:** Verify API_BASE constant in config.js points to correct backend

### Register snapshot empty in exported markdown
- **Check:** Was "Include register snapshot" checkbox checked when creating note?
- **Check:** Was SSH connected to SOM at time of note creation?
- **Fix:** Re-add note with snapshot checkbox enabled + SSH connected

### Profile name shows "(No profile selected)"
- **Expected:** When no profile is selected in dropdown
- **Fix:** Select a profile from dropdown before adding note (or edit note text manually)

---

**Version:** 1.0
**Date:** 2026-02-17
**Status:** ✅ Implemented and ready for testing
