# Classroom Website Update Script

Bulk update script for a cram school classroom website CMS.

## Features

- Logs into the CMS and updates all content fields with styled HTML
- Manages instructor profiles, campus info, campaigns, exam results, timetables, etc.
- Saves as draft or submits for approval

## Setup

```bash
pip install -r requirements.txt
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `SCHOOLIE_USERNAME` | CMS login username |
| `SCHOOLIE_PASSWORD` | CMS login password |

## Run

```bash
export SCHOOLIE_USERNAME="your_username"
export SCHOOLIE_PASSWORD="your_password"
python schoolie_update.py
```

## Files

- `endpoints.md` - CMS API endpoint reference (field list)
