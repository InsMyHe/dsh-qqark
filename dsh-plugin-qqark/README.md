# dsh-plugin-qqark

A DeepSeek Harness (DSH) plugin that turns the local [QQ group-file / chat-video archiver](https://github.com/) into an in-harness experience: a sidebar panel plus four read-only tools the agent can call mid-conversation.

> This package is the **plugin half** of a two-part project. The archiver itself (`main.py`, `qqark/`, pure Python 3.11 stdlib) lives in the repository root; the plugin only ever talks to it over `http://127.0.0.1:7801`.

## Compatibility

| Surface | Status |
|---|---|
| Harness | DeepSeek Harness `0.2.1-rc.5` and later `0.2.x` |
| Node | `^22.19.0 \|\| >=24.0.0` |
| Platforms | Web client; archiver side is Windows-only (uses `explorer /select,`) |

## What it does

The archiver is a long-running process that captures QQ group files and chat videos. DSH may be closed, restarted, or busy — capturing must not depend on it. So the split is deliberate: **the archiver does the work, the plugin is a bridge.**

### Architecture: why a bridge and not everything-in-the-plugin

- The archiver must keep recording while DSH is closed. A plugin only lives as long as the harness process, so it cannot own the capture loop.
- The plugin talks to the archiver over its local HTTP API. Nothing is duplicated, no database is opened from Node.
- **Every tool exposed to the agent is read-only.** All mutating actions (scan now, retry failures, toggle groups, switch QQ accounts) are reachable only by a human clicking the sidebar panel.

### Tools

| Tool | Returns |
|---|---|
| `qqark_status` | connection state, logged-in accounts, save-root writability, queue counters, lifetime totals |
| `qqark_groups` | every known group with its capture switch, member count, last-scan time |
| `qqark_files` | downloaded files filtered by keyword / group / category / source, with local paths and browser-playable media URLs |
| `qqark_tasks` | downloader queue plus the most recent tasks and their failure reasons |

All four degrade gracefully: if the archiver is not running, they return a hint telling the user to launch `启动.bat` rather than a stack trace.

### Sidebar panel

A panel in the DSH web client with a status header, group list with per-group switches, file browser, download queue, log viewer, and an "archiver mode" card that can start the archiver process and flip NapCat between the archive account and the daily-driver account.

### Notable fixes worth knowing about

- **`file_count` must be passed explicitly.** NapCat's `get_group_root_files` / `get_group_files_by_folder` default to 50 items; the internal pagination loop breaks as soon as `r.length === n.fileCount`. Listing a large group therefore silently truncates, and the truncation used to cascade into irreversible `gone` verdicts. Fix: always send `LIST_FILE_COUNT = 1000`.
- **`gone` is a terminal state, distinct from `failed`.** A file is only marked `gone` when all three hold: refreshing its `file_id` fails, the scan produced no errors, and the name is absent from the whole tree. A partial directory read keeps the task retryable.
- **Never `spawn` the archiver with `detached: true` on Windows.** In the measured matrix, every `detached: true` combination exited within a second; the working approach is `cmd /c start`.
- **Read back after launching.** Spawning a process proves nothing. The plugin polls the archiver's `/api/status` until it actually answers before reporting success.
- **Host routes need a leading `/`; browser `fetch` must not have one.** The GUI is served under `<base href="./">`, so an absolute path in a `fetch` call misses the plugin route prefix (DSH issue #1707).
- **IPv6 hosts keep their brackets.** `new URL('ws://[::1]:6200/ws').hostname` yields `[::1]` including brackets; strip them before comparing or probing.

## Install

```sh
pnpm pack
dsh plugin --profile <name> add ./dsh-plugin-qqark-<version>.tgz
dsh --profile <name> --dump-config | grep 'dsh-plugin-qqark'
```

The bundle patch (`cordis.patch.yml`) is applied automatically through `package.json#dsh.bundle.patch`. Prerequisites on the machine:

1. NapCat running and logged in to a QQ account
2. The archiver (`main.py`) running, reachable at `http://127.0.0.1:7801`
3. `grabAccount` / `quietAccount` filled in the plugin settings — they ship empty on purpose, because a default would silently point at somebody else's account

## Configuration

| Key | Type | Default | Description |
|---|---|---|---|
| `baseUrl` | string | `http://127.0.0.1:7801` | Web address of the local archiver |
| `timeoutMs` | natural | `8000` | Per-request HTTP timeout in milliseconds |
| `appDir` | string | `''` | Archiver program directory (the one holding `启动.bat`); empty means auto-detect |
| `grabAccount` | string | `''` | QQ account dedicated to archiving; empty disables the archiver-mode switch |
| `quietAccount` | string | `''` | Daily-driver QQ account to switch back to; empty disables the switch |
| `napcatConfigDir` | string | `''` | NapCat's `napcat/config` directory; empty means auto-detect |
| `wsPort` | natural | `6200` | Port the archiver binds for NapCat's reverse WebSocket |
| `astrbotPort` | natural | `6199` | The external-bot port that archiver mode disconnects |

Configuration is validated by the Schemastery `Config` schema in `index.js`; no tunable is hardcoded. There is deliberately **no** default QQ account — the plugin refuses to enable archiver mode until both accounts are set, and says so.

## Development

```sh
pnpm install
pnpm test
```

There is no build step: the package ships plain ESM (`index.js`, `client.js`, `napcat.js`) and is loaded directly by the harness. To validate the package contract before publishing:

```sh
node <dsh-plugin-guide>/bin/dsh-plugin-dev.js check --cwd .
```

Layout:

```
index.js          host half: config schema, HTTP bridge, host routes, 4 tools
client.js         web half: sidebar panel components (hand-written h(), no JSX)
napcat.js         NapCat discovery, launch, login and OB11 client hot-reload
cordis.patch.yml  bundle patch inserting the plugin row
locale/           zh.json, en.json
```

## License

[MIT](../LICENSE).
