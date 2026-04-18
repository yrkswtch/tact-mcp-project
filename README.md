# tact-mcp-project

MCP (Model Context Protocol) servers for automating cram school (School IE) classroom operations.

Built for Claude Code. Handles student management, inquiry tracking, website content updates, and PCS test system.

## Install as Plugin

```bash
/plugin install tact-mcp@yrkswtch/tact-mcp-project
```

Or test locally:
```bash
claude --plugin-dir ./tact-mcp-project
```

On install, you'll be prompted for your classroom credentials (SKS, WebSupport, Schoolie-net).

## Project Structure

```
tact-mcp-project/
├── .claude-plugin/        # Plugin manifest
├── .mcp.json              # MCP server definitions
├── skills/                # Claude Code skills (auto-registered)
│   ├── sks-login/         # SKS auto-login
│   ├── sks-student-info/  # Student info viewer
│   ├── sks-inquiry/       # Inquiry registration
│   ├── sks-video/         # Video system SSO
│   ├── sks-pcs/           # PCS test operations
│   ├── websupport-ops/    # WebSupport operations
│   ├── classroom-hp-update/ # Classroom website CMS
│   └── nks-nippou/        # Daily report (NKS only)
├── servers/               # MCP server implementations
│   ├── sks/               # WEB-SKS student management
│   ├── websupport/        # WEB SUPPORT inquiry management
│   └── classroom-web-update/ # Schoolie-net CMS
├── docs/                  # Endpoint specs & GUI automation notes
└── scripts/               # Utility scripts (NKS daily report)
```

## Manual Setup (without plugin)

If not using the plugin system, register each MCP server manually:

```bash
# SKS
claude mcp add sks \
  -e SKS_BASE_URL=http://tacs.tacsvpn \
  -e SKS_SSK2_URL=http://ssk2.tacsvpn \
  -e SKS_ACCOUNT=your_account \
  -e SKS_PASSWORD=your_password \
  -e SKS_CLASSROOM=your_code \
  -- python3 /path/to/servers/sks/server.py

# WebSupport
claude mcp add tact-websupport \
  -e WEBSUPPORT_ACCOUNT=your_account \
  -e WEBSUPPORT_PASSWORD=your_password \
  -- python3 /path/to/servers/websupport/server.py

# Schoolie-net
claude mcp add schoolie-net \
  -e SCHOOLIE_USERNAME=your_username \
  -e SCHOOLIE_PASSWORD=your_password \
  -- python3 /path/to/servers/classroom-web-update/server.py
```

## Tech Stack

- Python 3.12+
- [FastMCP](https://github.com/jlowin/fastmcp) - MCP server framework
- requests + BeautifulSoup4 - Web scraping
- pycryptodome - AES encryption (SKS login)

## Documentation

| File | Description |
|------|-------------|
| [docs/sks-endpoints.md](docs/sks-endpoints.md) | SKS HTTP API specification |
| [docs/sks-gui-automation.md](docs/sks-gui-automation.md) | SKS browser automation notes |
| [docs/websupport-endpoints.md](docs/websupport-endpoints.md) | WebSupport API specification |
| [docs/websupport-gui-automation.md](docs/websupport-gui-automation.md) | WebSupport browser automation notes |
| [docs/classroom-endpoints.md](docs/classroom-endpoints.md) | Schoolie-net CMS API specification |
| [docs/classroom-gui-automation.md](docs/classroom-gui-automation.md) | Schoolie-net browser automation notes |

<sub><sup>yaruki switch group / tact group / tact corporation / school IE / スクールIE / やる気スイッチグループ / やる気スイッチ / 拓人 / 株式会社拓人</sup></sub>
