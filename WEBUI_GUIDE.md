# Web UI Quick Start

## What's New

Added a beautiful web interface for running metadata checks without CLI knowledge:

- **Drag-and-drop** file upload
- **Real-time** check execution
- **Interactive** results dashboard
- **No S3 access needed** — local file mode

## Files Added

- `src/metadata_checker/web.py` — Flask app & API routes
- `src/metadata_checker/templates/index.html` — UI layout
- `src/metadata_checker/static/style.css` — Styling
- `src/metadata_checker/static/app.js` — Interactive behavior
- `run_web.py` — Simple startup script

## How to Run

### 1. Activate virtual environment (if not already active)
```bash
source .venv/bin/activate
```

### 2. Start the web server
```bash
python run_web.py
```

You'll see:
```
==================================================
Metadata Checker Web UI
==================================================

🌐 Open your browser: http://127.0.0.1:5000

Press CTRL+C to stop the server
```

### 3. Open browser
Go to: **http://127.0.0.1:5000**

## Using the Interface

### Original Checks Tab
1. Click "Select Original JSON Files"
2. Choose one or more JSON files from your computer
3. Click "Run Original Checks"
4. View findings in the dashboard

### Converted Checks Tab
1. Click "Select Original JSON Files" (top)
2. Choose original JSON files
3. Click "Select Converted JSON Files" (bottom)
4. Choose converted JSON files  
5. Click "Run Converted Checks"
6. Compare findings

## Results Dashboard

Each report shows:
- **Total Findings**: Count of all issues found
- **By Severity**: High/Medium/Low breakdown
- **Records Checked**: How many JSON objects were analyzed
- **Findings List**: Detailed issue cards with:
  - Check ID (what was tested)
  - Severity (color-coded)
  - Message (what's wrong)
  - File reference
  - Content ID (if available)

**Color coding:**
- 🔴 **High**: Critical data quality issues
- 🟠 **Medium**: Should be reviewed
- 🔵 **Low**: Nice-to-have improvements

## Next Steps

1. **Test locally first** — use sample JSON files
2. **Tune config** — edit `configs/rules.example.yaml` if field names don't match
3. **Share findings** — download/screenshot the dashboard
4. **When S3 access ready** — switch to CLI S3 mode or ask to extend web UI with S3 upload

## Troubleshooting

**Port 5000 already in use?**
```bash
python run_web.py  # Try stopping other Flask servers or restart terminal
```

**Files not uploading?**
- Make sure files are valid JSON
- Check browser console (F12 → Console tab) for errors

**No findings found?**
- Verify JSON structure matches your config in `configs/rules.example.yaml`
- Start with a small sample file to debug

## Architecture

```
Browser → HTTP → Flask Routes
                    ↓
              Metadata Checker Engine
                    ↓
              Read JSON → Validate Rules → JSON Report
                    ↓
              Return to Browser Dashboard
```

All processing happens **locally** — nothing goes to external servers.
