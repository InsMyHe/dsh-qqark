# dsh-plugin-qqark

DeepSeek Harness（DSH）插件：把本机的 [QQ 群文件 / 聊天视频归档器](https://github.com/) 搬进 harness 里——一个左侧边栏面板，加四个 agent 可以在对话中直接调用的只读工具。

> 本包是两部分项目中的**插件那一半**。归档器本体（`main.py`、`qqark/`，纯 Python 3.11 标准库）在仓库根目录；插件只通过 `http://127.0.0.1:7801` 跟它说话。

## Compatibility

| 面 | 状态 |
|---|---|
| Harness | DeepSeek Harness `0.2.1-rc.5` 及之后的 `0.2.x` |
| Node | `^22.19.0 \|\| >=24.0.0` |
| 平台 | Web 客户端；归档器一侧仅 Windows（用了 `explorer /select,`） |

## What it does

归档器是常驻进程，负责抓 QQ 群文件和聊天视频。DSH 可能关闭、重启、正忙——抓取不能依赖它。所以分工是刻意的：**归档器干活，插件只当桥。**

### 为什么是「桥」而不是全塞进插件

- 归档器必须做到 DSH 关掉也照录。插件的生命周期等于 harness 进程，扛不起抓取循环。
- 插件走归档器的本地 HTTP API。不复制数据，也不从 Node 里打开数据库。
- **暴露给 agent 的工具全部只读。** 所有会改动状态的动作（立即补扫、重试失败、开关群、切换 QQ 号）只能由人点侧边栏面板触发。

### 提供的工具

| 工具 | 返回什么 |
|---|---|
| `qqark_status` | 连接状态、已登录账号、保存目录是否可写、队列计数、累计统计 |
| `qqark_groups` | 全部已知群及其抓取开关、人数、上次扫描时间 |
| `qqark_files` | 已下载文件，可按关键词 / 群号 / 类别 / 来源过滤，附本地路径与浏览器可播的媒体链接 |
| `qqark_tasks` | 下载队列与最近任务明细，含失败原因 |

四个工具都会优雅降级：归档器没在跑时，返回一句「请先双击 启动.bat」的提示，而不是抛堆栈。

### 侧边栏面板

DSH Web 客户端里的一个面板：状态头、带开关的群列表、文件浏览、下载队列、日志查看，以及「归档模式」卡片——能直接拉起归档器进程，并在归档专用号与日常号之间切换 NapCat 的登录。

### 几个值得知道的坑（都已修）

- **列群文件必须显式传 `file_count`。** NapCat 的 `get_group_root_files` / `get_group_files_by_folder` 默认只回 50 条，内部翻页的退出条件是 `r.length === n.fileCount`。大群因此被静默截断，而截断又会级联成不可逆的 `gone` 误判。修法是固定传 `LIST_FILE_COUNT = 1000`。
- **`gone` 是终态，必须和 `failed` 分开。** 只有三条同时成立才判 `gone`：换不到新 `file_id`、扫描没报错、整棵树里查无此名。目录只读了一半时任务保持可重试的 `failed`。
- **Windows 上绝不能用 `detached: true` 启动归档器。** 实测矩阵里，`detached: true` 的每一种组合都在一秒内退出；可用的是 `cmd /c start`。
- **启动完一定要回读验证。** spawn 成功不代表进程活着。插件会轮询归档器的 `/api/status`，直到它真的应答才报成功。
- **宿主路由要带前导 `/`，浏览器 `fetch` 反而不能带。** GUI 挂在 `<base href="./">` 下，`fetch` 里写绝对路径会绕过插件路由前缀（DSH issue #1707）。
- **IPv6 主机的方括号要剥掉。** `new URL('ws://[::1]:6200/ws').hostname` 出来的是带方括号的 `[::1]`，比较或探测前要去掉。

## Install

```sh
pnpm pack
dsh plugin --profile <name> add ./dsh-plugin-qqark-<version>.tgz
dsh --profile <name> --dump-config | grep 'dsh-plugin-qqark'
```

bundle patch（`cordis.patch.yml`）通过 `package.json#dsh.bundle.patch` 自动生效。机器上的前提：

1. NapCat 已启动并登录了 QQ
2. 归档器（`main.py`）在跑，`http://127.0.0.1:7801` 可达
3. 在插件设置里填好 `grabAccount` / `quietAccount`——它们刻意留空，因为有默认值等于悄悄指向别人的号

## Configuration

| 键 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `baseUrl` | string | `http://127.0.0.1:7801` | 本机归档器的 Web 地址 |
| `timeoutMs` | natural | `8000` | 单次 HTTP 请求超时（毫秒） |
| `appDir` | string | `''` | 归档器程序目录（含 `启动.bat`）；留空则自动查找 |
| `grabAccount` | string | `''` | 归档专用 QQ 号；留空则归档模式开关不可用 |
| `quietAccount` | string | `''` | 要切回的日常 QQ 号；留空则归档模式开关不可用 |
| `napcatConfigDir` | string | `''` | NapCat 的 `napcat/config` 目录；留空则自动查找 |
| `wsPort` | natural | `6200` | 归档器接收 NapCat 反向 WS 的端口 |
| `astrbotPort` | natural | `6199` | 归档模式会切断的那个外部机器人端口 |

配置由 `index.js` 里的 Schemastery `Config` 校验，没有任何硬编码调参。**刻意不提供默认 QQ 号**——两个账号没填全之前，插件会拒绝打开归档模式，并明确告诉你原因。

## Development

```sh
pnpm install
pnpm test
```

没有构建步骤：包里直接是纯 ESM（`index.js`、`client.js`、`napcat.js`），由 harness 直接加载。发布前校验包契约：

```sh
node <dsh-plugin-guide>/bin/dsh-plugin-dev.js check --cwd .
```

目录：

```
index.js          宿主半边：配置 schema、HTTP 桥、宿主路由、4 个工具
client.js         Web 半边：侧边栏面板组件（手写 h()，不用 JSX）
napcat.js         NapCat 探测、启动、登录与 OB11 客户端热重载
cordis.patch.yml  bundle patch，插入插件那一行
locale/           zh.json、en.json
```

## License

[MIT](../LICENSE)。
