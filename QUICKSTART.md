# 🚀 Quick Start Guide

Your metadata checker is ready. Choose your path below.

---

## Option 1️⃣  — Web UI (⭐ Recommended to Start)

**Best for:** Learning, testing, no CLI needed

### Steps:
```bash
# 1. Activate environment
source .venv/bin/activate

# 2. Start server
python run_web.py

# 3. Open browser
# → http://127.0.0.1:5000
```

### What to do:
- Drag & drop JSON files into the upload box
- Click "Run Original Checks" or "Run Converted Checks"
- View findings in the beautiful dashboard

### Test with samples:
```bash
# Original checks test
# Upload: sample_data/original_sample.json
# Should find: missing images, empty genres, etc.

# Converted checks test
# Upload original: sample_data/original_sample.json
# Upload converted: sample_data/converted_sample.json
# Should find: mapping mismatches
```

---

## Option 2️⃣  — CLI Local Mode

**Best for:** Batch processing, automation, when web UI not available

### Steps:
```bash
source .venv/bin/activate

# Original checks
metadata-checker run-original \
  --mode local \
  --local-path ./sample_data \
  --config configs/rules.example.yaml \
  --output reports/original_report.json

# Converted checks
metadata-checker run-converted \
  --mode local \
  --original-local-path ./sample_data \
  --converted-local-path ./sample_data \
  --config configs/rules.example.yaml \
  --output reports/converted_report.json
```

Reports saved as JSON in `reports/` folder.

---

## Option 3️⃣  — CLI S3 Mode (Future)

**When:** You have AWS sandbox access

### Steps:
1. Edit `terraform/terraform.tfvars` with your S3 bucket details
2. Run `terraform init && terraform apply` in `terraform/` folder
3. Export AWS credentials to shell
4. Run CLI with `--mode s3` flag

See [README.md](README.md) for full S3 setup.

---

## Understanding Your Config

Edit **`configs/rules.example.yaml`** to match your JSON schema.

Key sections:
- **id_keys**: Which field has content ID?
- **type_keys**: Which field has content type (movie/series)?
- **candidate_keys**: Maps check fields to your JSON paths

Example:
```yaml
candidate_keys:
  title:
    - title          # Try this first
    - name           # If not found, try this
    - titles.main    # Or nested like this
```

---

## Viewing Results

### Web UI:
- See findings live in dashboard
- Color-coded by severity (red=high, orange=medium, blue=low)
- Click to expand details

### JSON Report:
```bash
cat reports/original_report.json | jq '.'
```

Structure:
```json
{
  "records_checked": 25,
  "summary": {
    "total_findings": 8,
    "by_severity": { "high": 3, "medium": 4, "low": 1 }
  },
  "findings": [
    { "check_id": "...", "severity": "...", "message": "..." }
  ]
}
```

---

## Troubleshooting

**Q: Port 5000 already in use?**
```bash
# Check what's on port 5000
lsof -i :5000

# Or use a different port
python -c "from metadata_checker.web import create_app; create_app().run(port=5001)"
```

**Q: Import errors after updating?**
```bash
pip install -e . --force-reinstall
```

**Q: Don't see my custom fields in findings?**
- Edit `configs/rules.example.yaml` with your JSON keys
- Restart web server or rerun CLI
- Try a small sample file first to debug

**Q: How do I add more checks?**
- Edit [src/metadata_checker/checker.py](src/metadata_checker/checker.py)
- Follow the pattern of existing checks
- Restart server

---

## Next Steps

1. **Try Web UI first** — faster feedback loop
2. **Test with sample data** — `sample_data/` folder has examples
3. **Run with your real metadata** — update `configs/rules.example.yaml` as needed
4. **When ready** — switch to CLI for batch/automated runs
5. **Later** — integrate S3 when sandbox access arrives

---

## File Map

```
vol2/
├── run_web.py                 ← Start here for web UI
├── README.md                  ← Full documentation
├── WEBUI_GUIDE.md            ← Web UI details
├── SAMPLE_REPORT.json        ← Example output
├── configs/
│   └── rules.example.yaml    ← ⚙️  Tune this for your schema
├── sample_data/
│   ├── original_sample.json  ← Test data
│   └── converted_sample.json ← Test data
├── src/metadata_checker/
│   ├── web.py               ← Flask app
│   ├── cli.py               ← Command-line interface
│   └── checker.py           ← Core logic
├── reports/                 ← Generated JSON reports
└── terraform/               ← AWS setup (for later)
```

---

## Support

**Running into issues?**
- Check [WEBUI_GUIDE.md](WEBUI_GUIDE.md) for web UI help
- See [README.md](README.md) for CLI help
- Review [sample_data/](sample_data/) for example JSON structure
- Check [SAMPLE_REPORT.json](SAMPLE_REPORT.json) to see what findings look like

**Ready to learn?**
Start with the web UI, upload sample files, and watch findings appear! 🎉
