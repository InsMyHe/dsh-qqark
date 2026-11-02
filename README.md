# QQ 群文件 / 视频归档器 + DeepSeek Harness 插件

把 QQ 群里**新上传的群文件**和**聊天里直接发的视频**自动下载到本地，按「群 → 年月 → 类型」分好目录，配一个本地网页随时浏览播放；再给 [DeepSeek Harness](https://github.com/deepseek-ai/) 配一个插件，让人工智能直接在对话里查归档状态。

本仓库是 monorepo，两部分可以分别使用：

| 目录 | 是什么 | 依赖 |
|---|---|---|
| `qqark/`、`main.py`、`启动.bat` | **归档器**：常驻进程，连 NapCat 收事件、下载、建索引、开本地媒体库 | Python 3.11+，零第三方库 |
| `dsh-plugin-qqark/` | **DSH 插件**：把归档器包成宿主侧边栏面板 + 4 个只读工具 | Node 22.19+ / 24+，DeepSeek Harness |

```
NapCat（QQ 协议端）  ──反向 WebSocket──▶  归档器 main.py  ──HTTP──▶  DSH 插件
       ws://127.0.0.1:6200/ws                http://127.0.0.1:7801
```

## 特点

- **零第三方依赖**：归档器是纯 Python 3.11 标准库，不用 `pip install` 任何东西
- **绝不发言**：OneBot 客户端层内置写操作黑名单，`send_*`、`set_group_*`、`upload_group_file` 一类调用直接抛异常。它只读、只下载
- **不抢 NapCat 现有配置**：只**追加**一条指向 `127.0.0.1:6200` 的反向 WS 客户端，原来指向 AstrBot（`6199`）的那条原样保留，两边并存
- **只抓你指定的群**：所有群都在列表里，只有你打开开关（或写进 `watch_groups`）的群才会被扫描下载
- **实时 + 补扫**：事件来了立刻下；软件没开期间漏掉的群文件靠定时补扫找回
- **本地媒体库**：`http://127.0.0.1:7801` 里搜索、筛选、直接播放（支持拖动进度条）
- **插件只读**：暴露给 agent 的 4 个工具全是只读查询；写操作只能由用户点侧边栏 UI 触发

## 快速开始（归档器）

1. **装好并启动 NapCat、登录 QQ**（保持它在运行）
2. 双击 **`启动.bat`**，或命令行 `python main.py`
3. 浏览器自动打开媒体库页面，顶部显示「已连接 · 你的昵称」
4. 在左侧群列表里，把要归档的群右侧开关打开

文件默认存到 `~/QQArk`（Windows 上是 `%USERPROFILE%\QQArk`），首次启动自动创建。目录结构：

```
QQArk\
  高等数学答疑群_111222333\
    2025-10\
      视频\     课程录像 第5次课.mp4
      文档\     第03讲 卷积神经网络.pdf
      压缩包\   源码合集.zip
```

> 第一次用如果显示「未连接 NapCat」，运行 `python main.py --check`。它会自动找到 NapCat 配置并告诉你怎么改（加 `--fix` 可直接改，改前会备份）。

> 只想盯几个群？点开关，或者编辑 `config.json` 的 `watch_groups`：
> ```json
> "watch_groups": ["123456789", "987654321"]
> ```
> 列表一旦非空，就**只有**列表里的群会被扫描；同时该群还要在群列表里开着开关（两个条件都满足才抓）。

配置模板见 [`config.example.json`](config.example.json)——复制成 `config.json` 再改，或直接让程序首次运行时自己生成。

## 命令行参数

```
python main.py                  启动（自动开浏览器）
python main.py --no-browser     启动但不打开浏览器
python main.py --check          只检查 NapCat 配置，不启动
python main.py --fix            检查并自动修好 NapCat 配置
python main.py --mock           用演示数据看界面（不需要 NapCat）
python main.py --port 6200      换个 WS 端口
python main.py --web-port 7801  换个网页端口
python main.py --save-root D:\QQ归档   换保存目录
python main.py --log DEBUG      输出调试日志
```

## 界面

| 区域 | 功能 |
|---|---|
| 顶部状态灯 | 绿=已连 NapCat，红=没连上 |
| 左侧群列表 | 点群名筛选；右侧开关控制该群是否监听 |
| 搜索框 | 按文件名 / 群名 / 上传者模糊搜索 |
| 分类标签 | 视频 / 文档 / 压缩包 快速过滤 |
| 来源标签 | 区分「群文件」和「聊天视频」 |
| 卡片按钮 | 播放 / 打开、在资源管理器里定位 |
| 补扫群文件 | 立刻扫一遍所有群的群文件目录 |
| 下载队列 | 看排队 / 进行 / 失败，可一键重试 |
| 设置 | 保存位置、目录布局、抓取范围、并发数、补扫间隔 |

视频卡片点开就能播放（HTTP Range 支持拖动）。非媒体文件点「目录」用系统默认程序打开。


## 能抓到什么

| 来源 | 是否抓 | 说明 |
|---|---|---|
| 群文件（所有类型） | 默认开 | 有人往群文件里传东西就下 |
| 聊天里直接发的视频 | 默认开 | 链接几分钟就失效，必须秒下 |
| 聊天里直接发的文件 | 默认开 | 消息里的 `file` 段 |
| 图片 | 默认关 | 量大，可在设置里打开 |
| 语音 / 表情 / 合并转发 | 不做 | — |

**为什么视频必须实时抓**：QQ 的视频下载链接带 rkey，通常几分钟到几小时就作废。收到消息事件后立刻入队下载；如果当时软件没开，事后基本救不回来（群文件不受此限，补扫能找回）。

## 目录布局（设置里四选一）

| 布局 | 效果 |
|---|---|
| `group_date_type` **推荐** | 群名_ID/2025-10/视频/xxx.mp4 |
| `group_type` | 群名_ID/视频/xxx.mp4 |
| `date_type` | 2025-10/视频/xxx.mp4 |
| `type_only` | 视频/xxx.mp4 |

同名文件不会覆盖：自动加时间戳和上传者前缀，仍冲突再加 `_2`、`_3`。

## 可靠性设计

- **断点续传**：先写 `data/tmp/*.part`，进程被杀重启后带 `Range` 续传
- **链接过期自愈**：遇 403/404 自动重新调 API 换新链接再试，默认重试 3 次
- **原子落盘**：下载完先校验大小，再用 `os.replace` 挪到目标目录，不会出现半个文件
- **去重**：按 `(群号, 文件名, 字节数)` 算稳定键 `dedupe_key` 查重。**不能用 `file_id`**——NapCat 每次列举群文件都会给同一个文件生成新的 `file_id`，用它去重会导致同一文件被反复下载（实测踩过的坑）。`file_id` 只用来临时换下载链接
- **重启恢复**：上次没跑完的任务自动放回队列
- **端口占用自愈**：网页端口被占自动顺延到下一个
- **单实例保护**：WS 端口绑定失败时直接退出，绝不持锁空转

数据都在 `data/`：`qqark.db`（SQLite 索引）、`qqark.log`（完整日志）、`tmp/`（下载中）、`thumbs/`（缩略图缓存）。

## 常见问题

**Q：显示未连接 NapCat？**
运行 `python main.py --check`。它会找到 NapCat 的 `onebot11_<QQ号>.json`，检查「WebSocket 客户端」里有没有指向 `ws://127.0.0.1:6200/ws` 的一条，没有就追加（先备份，**不动**你原有的其它客户端）。改完要**重启 NapCat**。

> **为什么是 6200 不是 6199**：6199 是 AstrBot 的 aiocqhttp 反向 WS 默认端口。在同一个端口上抢绑定，Windows 会允许两个进程都「绑定成功」，但 NapCat 的连接只会交给其中一个——表现就是软件显示已启动却永远收不到事件。用独立端口、两条客户端并存，才是可靠做法。

**Q：群列表是空的？**
群列表来自 NapCat，连上后点「刷新」，刚启动要几秒拉取。

**Q：视频没有缩略图？**
视频抽帧需要 `ffmpeg`。没有就显示图标按钮，不影响播放。把 `ffmpeg.exe` 放到项目 `bin/` 下即可自动启用。图片缩略图同理。

**Q：能不能抓历史聊天记录里的视频？**
不能。那些链接早就失效，QQ 服务器上也没有可下载的原始文件。软件只保证「从现在起」不漏。

**Q：关掉窗口就停止抓取了吗？**
是的。要长期归档就让窗口一直开着（可最小化）。想开机自启，把 `启动.bat` 的快捷方式放进 `shell:startup`。

**Q：它会不会在群里说话 / 自动回复？**
不会。写操作黑名单在 `qqark/onebot.py` 的 `_WRITE_ACTIONS`，任何 `send_*`、`set_group_*`、`upload_group_file` 直接抛异常，根本发不出去。

> 但同一个 QQ 号如果还连着别的机器人（比如 AstrBot），那个机器人照样会说话。想让号彻底安静，要单独把 AstrBot 停掉或禁用它的 platform。这与本软件无关。

**Q：保存目录在 C 盘会不会爆？**
设置里有「跳过大于 (MB)」过滤大文件；也可以用 `--save-root` 改到别的盘。索引里的路径不变，不影响已下载的文件。

## 项目结构

```
main.py              启动器 / 命令行
selftest.py          端到端自检（不需要 NapCat，37 项）
启动.bat             双击启动（全 ASCII + CRLF，改动前请读第九节）
config.example.json  配置模板
qqark/
  app.py             装配层：把下面这些拼起来
  ws_server.py       零依赖 WebSocket 服务端（NapCat 反向连进来）
  onebot.py          OneBot11 客户端（WS + HTTP 兜底，含写操作黑名单）
  events.py          事件路由：group_upload / 聊天视频 → 下载任务
  scanner.py         补扫器：定时遍历群文件目录找回漏掉的
  downloader.py      下载队列：并发、续传、重试、去重、原子落盘
  classify.py        分类与路径生成
  config.py          配置读写
  db.py              SQLite 索引
  web.py             HTTP 服务 + Range + 缩略图
  webui.py           前端单页（内联，无外部资源）
  napcat.py          NapCat 配置探测与自动修复
  mock.py            演示数据
tests/               回归测试（5 个）
scripts/             仓库自检脚本（CI 也跑同一套）
dsh-plugin-qqark/    DeepSeek Harness 插件的源码与文档
market/              DSH 插件市场索引（format 1），把插件挂进「发现」页
docs/screenshots/    界面截图
```

## 测试

```sh
python selftest.py                  # 端到端自检，37 项，不需要 NapCat
python tests/test_file_count.py     # 列群文件必须显式传 file_count
python tests/test_fileid_refresh.py # file_id 失效后能重新列举拿到新链接
python tests/test_gone_state.py     # gone 是终态，与可重试的 failed 分开
python tests/test_offline_nowork.py # 未连接时任务原地不动
python tests/test_startup_guard.py  # 端口被占时干净退出，不持锁

# 仓库自检（CI 里跑的同一套）
node scripts/check-plugin-contract.mjs dsh-plugin-qqark
python scripts/check_launcher.py 启动.bat
python scripts/check_no_personal_paths.py
```

`check_no_personal_paths.py` 会把 `~` 之外写死的用户目录、以及 `QQARK_FORBIDDEN`
环境变量里列出的值当作泄漏（词表放在环境变量里，仓库本身不存要防的字面量）。

## 插件部分

见 [`dsh-plugin-qqark/README-zh.md`](dsh-plugin-qqark/README-zh.md)（另有 [English](dsh-plugin-qqark/README.md) / [Español](dsh-plugin-qqark/README-es.md) / [Português](dsh-plugin-qqark/README-pt.md) / [हिन्दी](dsh-plugin-qqark/README-hi.md)）。一句话：它把归档器包成一个侧边栏面板，并注册 `qqark_status`、`qqark_groups`、`qqark_files`、`qqark_tasks` 四个只读工具，让 agent 能在对话里直接回答「上周那个群传了什么」「下载队列卡了没」。

## 插件市场

仓库里的 `market/` 是一份符合 DSH 插件市场索引 format 1 的 JSON，把插件挂进 Harness 的「发现」页，省掉手动 `pnpm pack` + `dsh plugin add`。

1. DSH → **插件** → **实验性插件** → 更多 → **自定义插件市场**，打开开关
2. 插件页出现「发现」标签 → **添加索引源**，粘贴：

   ```
   https://cdn.jsdelivr.net/gh/InsMyHe/dsh-qqark@main/market/index.json
   ```

3. 在列表里点「QQArk 归档器插件」安装即可。

为什么走 jsDelivr 而不是 `raw.githubusercontent.com`：DSH 抓索引只给 15 秒，`raw` 在本机实测多次 `502` / `SSLEOFError`，jsDelivr 稳定且支持 `@main` 与 `@<commit>` 两种 ref，也支持子目录。

索引里的安装源是**钉死在 commit 上的 git spec**：

```
git+https://github.com/InsMyHe/dsh-qqark.git#<commit>&path:/dsh-plugin-qqark
```

改了插件版本就要同步更新 `market/index.json` 里的 `versions[]`（`version` / `publishTimestamp` / `source.commit` 三处一起改），否则别人装到的是旧代码。发布前用仓库自带脚本按 format 1 规则过一遍：

```sh
node scripts/check-market-index.mjs
```

它会逐条对齐官方 `@deepseek-ai/dsh-plugin-market-index` 的校验规则，检查 commit 是否 40 位小写十六进制、图标能否解析、category/label 是否已声明等。

## 已知边界

- 只支持**群**，私聊不做
- 聊天视频漏了就漏了（rkey 机制决定）
- 消息为字符串格式（非 array）时跳过媒体段解析；NapCat 默认已配 array
- 视频缩略图依赖 ffmpeg，没有则降级为图标
- **只能同时用一个 QQ 号**：QQNT 9.9.33 的加载器内建单实例限制，复制一份 NapCat 目录启动第二份会报 `error code v2:-8` 并秒退，NapCat 启动器随后空等命名管道卡死。命令行没有 `--multi-instance` / `--user-data-dir` 之类的开关可绕（全部实测过）。软件自身支持多账号同时连接（一个账号一个连接、各抓各的群），只是同一台机器开不出第二个 QQ。换号用 NapCat WebUI 的快速登录
- Windows 专用（用了 `explorer /select,`），代码结构上没绑死，但没在别的系统测过

## 双击启动失败怎么排查

`启动.bat` 只做三件事：切 UTF-8 代码页、找 Python、调 `main.py`。它内部**全是 ASCII 字符且用 CRLF 换行**，这两点都是必须的：

- **换行必须 CRLF**：cmd.exe 逐行解析批处理，遇到 LF 会把 `if (...)` 多行块读成一整行，后面命令全部错乱，表现是满屏「系统找不到指定的路径」「'xxx' 不是内部或外部命令」
- **不能有中文**：`chcp 65001` 生效前 cmd 会先用当前代码页解释那一行，UTF-8 中文被当 GBK 解成乱码，乱码还会吞掉换行，把下一行命令粘进来

如果你用编辑器改过 `启动.bat` 然后它开始报错，把换行改回 CRLF、并保证文件里没有中文即可。仓库的 `.gitattributes` 已经声明了 `*.bat text eol=crlf`。

另外 `main.py` 开头会强制把 stdout/stderr 切成 UTF-8（`errors="replace"` 兜底）并把控制台代码页设为 65001。没有这一步，Windows 中文版的 GBK 控制台在打印 `✓` 这类字符时会抛 `UnicodeEncodeError: 'gbk' codec can't encode character '\u2713'` 并直接终止程序——`--check` 就踩过这个。

日志永远写在 `data/qqark.log`，出问题先看它。

## 许可

[MIT](LICENSE)。使用前请自行确认符合 QQ 相关服务条款与所在地区的法律法规；本项目只做本地归档，不修改、不上传任何数据。

[![CI](https://github.com/InsMyHe/dsh-qqark/actions/workflows/ci.yml/badge.svg)](https://github.com/InsMyHe/dsh-qqark/actions/workflows/ci.yml)
