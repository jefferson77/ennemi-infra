## Serena (code navigation and editing)

Every project on this machine has the Serena MCP server (`mcp__serena__*`
tools); it picks the project from the directory Claude Code was started in.

- **Load the tools first.** Serena's tools are deferred: on any coding task,
  load them with ToolSearch before the first read, grep or shell command, and
  call `initial_instructions` once per session before using them.
- **Code files go through Serena.** Get a file's shape with
  `get_symbols_overview`, read only the symbols you need with `find_symbol`
  (`include_body: true`), and follow usages with `find_referencing_symbols`.
  Edit with `replace_symbol_body`, `insert_before_symbol` /
  `insert_after_symbol`, or `replace_content` for a change inside a symbol;
  rename with `rename_symbol`. Grep and Glob are fine for discovery; the reads
  and edits that follow stay in Serena.
- **Everything else uses the built-in tools.** Markdown, YAML (Ansible
  included), JSON, TOML, `.env`, Makefiles and lockfiles have no
  symbols for Serena to work with: Read and Edit them directly. A project
  whose `.serena/project.yml` lists no `language_servers` (`ennemi-infra`) is
  all non-code in that sense.
- **When a Serena tool fails** (language server still starting, file not
  parsed), say so and fall back to the built-in tool for that step instead of
  retrying in a loop. Language servers are downloaded on a project's first
  use, so the first call can be slow.
