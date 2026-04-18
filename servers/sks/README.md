# SKS MCP Server

MCP server providing tools to interact with WEB-SKS, a student management system for cram school classrooms.

## Features

- Student roster retrieval and search
- Inquiry management (search & registration)
- PCS (Personal Curriculum System) operations: exam/answer PDF generation, scoring, curriculum registration
- Postal code reverse lookup (Japanese addresses)
- PDF printing via network printer

## Setup

```bash
pip install -r requirements.txt
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `SKS_BASE_URL` | SKS base URL (default: `http://tacs.tacsvpn`) |
| `SKS_SSK2_URL` | PCS/夢SEED server URL (default: `http://ssk2.tacsvpn`) |
| `SKS_ACCOUNT` | Login account ID |
| `SKS_PASSWORD` | Login password |
| `SKS_CLASSROOM` | Classroom code |

## Run

```bash
python server.py
```

## Files

- `endpoints.md` - SKS API endpoint reference
- `utf_ken_all.csv` - Japanese postal code database (from Japan Post)
