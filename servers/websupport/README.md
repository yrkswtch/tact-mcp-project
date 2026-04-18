# WebSupport MCP Server

MCP server providing tools to interact with WEB SUPPORT (tactgroup.net), an inquiry and enrollment management platform for cram school classrooms.

## Features

- Applicant management (list, search, detail, register, update, delete)
- Message box (list, search, detail)
- Contact notebook (inbox/sent messages)
- Student info, attendance, reward points
- Training videos & manuals
- OKS supplies and teaching materials ordering

## Setup

```bash
pip install -r requirements.txt
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `WEBSUPPORT_URL` | WebSupport base URL (e.g. `https://www.tactgroup.net`) |
| `WEBSUPPORT_ACCOUNT` | Login account ID |
| `WEBSUPPORT_PASSWORD` | Login password |

## Run

```bash
python server.py
```

## Files

- `endpoints.md` - WebSupport API endpoint reference
