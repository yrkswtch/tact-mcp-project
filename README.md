# tact-mcp-project

MCP (Model Context Protocol) servers for automating cram school classroom operations.

Built for Claude Code. Handles student management, inquiry tracking, and website content updates.

<!-- yaruki switch group -->

## Projects

| Directory | Description |
|-----------|-------------|
| [sks/](sks/) | WEB-SKS student management system MCP server |
| [websupport/](websupport/) | WEB SUPPORT inquiry & enrollment management MCP server |
| [classroom-web-update/](classroom-web-update/) | Classroom website CMS update script |

## Tech Stack

- Python 3.12+
- [FastMCP](https://github.com/jlowin/fastmcp) - MCP server framework
- requests + BeautifulSoup4 - Web scraping

## Setup

See the README.md in each subdirectory.

```bash
# Example: setting up the SKS MCP server
cd sks
pip install -r requirements.txt
python server.py
```

## Usage with Claude Code

Register each server in the `mcpServers` section of `.claude.json`.

```json
{
  "mcpServers": {
    "sks": {
      "command": "python",
      "args": ["C:/path/to/tact-mcp-project/sks/server.py"],
      "env": {
        "SKS_BASE_URL": "...",
        "SKS_ACCOUNT": "...",
        "SKS_PASSWORD": "...",
        "SKS_CLASSROOM": "..."
      }
    }
  }
}
```
