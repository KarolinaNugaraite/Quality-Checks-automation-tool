# Metadata Checks Tool (Original vs Converted)

This project helps you validate metadata quality in two stages:
- Original metadata checks
- Converted metadata checks (compared against original)

It is beginner-friendly and designed so you can learn Python + Terraform while building a real QA workflow.

## What this tool checks

Checks are split into two tiers:
- Must-pass structural checks (ERROR): intended for CI gating and channel integration hard-fail.
- Quality checks (WARNING/INFO): non-blocking quality feedback.

### Must-pass structural checks (CI gating)
- Missing mandatory envelope fields by metadata type (EPG/VOD/Event)
- Missing contentId based on configured id_keys
- Required broadcast/event field presence
- Duplicate broadcastId within channel/date scope
- Broadcast content reference that does not resolve to contents[]
- Invalid EPG from/to date format or invalid range ordering
- Invalid image URLs and invalid image dimensions
- Invalid ISO language/country codes
- VOD deeplink rule: required for sourceType=Deeplink, forbidden otherwise
- Invalid VOD ID format (regex from config)
- Missing publishing/broadcast rights and publishing targets

### Original metadata checks
- Missing titles for Movie/Series/Episode
- Missing descriptions for Movie/Series/Season/Episode
- Missing required description languages
- Empty/very short/placeholder descriptions
- Missing 16:9 or 2:3 images
- Missing or undefined image types
- Missing/empty genres
- Genre coverage by provider group/package
- Missing genres across hierarchy levels (Series/Season/Episode)
- Kids content missing Kids genre
- Missing production year
- Missing/zero/null duration for Movies/Episodes
- Missing age rating
- Missing deeplink
- Episode count outliers per series

### Converted metadata checks
- Title mapping mismatch (original vs converted)
- Description mapping mismatch
- Missing language blocks
- Image ratio mapping mismatch
- Image type mapping mismatch
- Genre mapping mismatch
- Missing hierarchy-level genres
- Kids genre not first
- Production year mismatch
- Duration mismatch/invalid values
- Age rating mismatch
- Deeplink mismatch/missing
- Special character/encoding issues
- Random spot checks on key fields
- Findings summary for recurring anomalies

## Project structure

- src/metadata_checker: Python source code
- configs/rules.example.yaml: check configuration and schema key mapping
- terraform: IaC for S3 read access setup
- reports: generated JSON reports

## Prerequisites

- Python 3.11+ recommended
- AWS account access to your bucket
- Terraform 1.5+

## Step 1: Python setup

From project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

**Windows/PowerShell users:**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
```

## Step 2: Option A — Web UI (Easiest for learning)

The web interface is perfect for testing without needing CLI knowledge.

```bash
python run_web.py
```

Open your browser to **http://127.0.0.1:5000** and you'll see:
- **Original Checks tab**: Upload original JSON files → get findings report
- **Converted Checks tab**: Upload both original and converted JSONs → get comparison report

Results display in a clean dashboard with:
- Summary statistics
- Severity breakdown (high/medium/low)
- Detailed findings with file and content ID references

This mode works **locally with file uploads** — no S3 access needed.

## Step 2: Option B — CLI (For automation & scripting)

Run CLI checks locally before S3 — faster feedback:

```bash
metadata-checker run-original \
  --mode local \
  --local-path /path/to/original/json \
  --config configs/rules.example.yaml \
  --output reports/original_report.json
```

```bash
metadata-checker run-converted \
  --mode local \
  --original-local-path /path/to/original/json \
  --converted-local-path /path/to/converted/json \
  --config configs/rules.example.yaml \
  --output reports/converted_report.json
```

## Step 3: Terraform setup for S3 access (optional, future step)

1. Go to terraform directory
2. Create tfvars file from example
3. Run init + plan + apply

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan
terraform apply
```

If create_iam_user is true, Terraform outputs AWS access keys.
Use them in your shell:

```bash
export AWS_ACCESS_KEY_ID="<value>"
export AWS_SECRET_ACCESS_KEY="<value>"
export AWS_DEFAULT_REGION="<your-region>"
```

## Step 4: Run against S3 (when sandbox access is available)

Run original checks:

```bash
metadata-checker run-original \
  --mode s3 \
  --bucket your-bucket-name \
  --prefix original/ \
  --sample-size 30 \
  --config configs/rules.example.yaml \
  --output reports/original_report.json
```

Run converted checks:

```bash
metadata-checker run-converted \
  --mode s3 \
  --bucket your-bucket-name \
  --original-prefix original/ \
  --converted-prefix converted/ \
  --sample-size 30 \
  --config configs/rules.example.yaml \
  --output reports/converted_report.json
```

## Understanding the report

Each report contains:
- summary.total_findings
- summary.by_check
- summary.by_severity
- findings: detailed records with check_id, message, file_ref, content_id

## Customize for your metadata schema

The checker is schema-agnostic. Adjust field keys in:
- configs/rules.example.yaml

Start by updating:
- id_keys
- type_keys
- candidate_keys.title
- candidate_keys.description
- candidate_keys.images
- candidate_keys.genre

## Learning path (recommended)

**Phase 1: Web UI (no setup)**
1. Run `python run_web.py`
2. Open http://127.0.0.1:5000
3. Upload 5–10 sample JSON files
4. Review findings in the dashboard
5. Iterate and tune config/field mappings

**Phase 2: CLI + Local**
1. Use CLI for batch processing
2. Generate JSON reports for automated workflows
3. Script multiple check runs

**Phase 3: S3 Integration (when access available)**
1. Configure Terraform
2. Switch to S3 mode in CLI
3. Run automated schedules

**Phase 4: Custom Logic**
1. Extend checks in [src/metadata_checker/checker.py](src/metadata_checker/checker.py)

## Safety note

Do not commit real credentials or Terraform state files.
Use least privilege for IAM access.
