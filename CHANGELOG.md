# Changelog

本项目两部分（归档器 + DSH 插件）共用一个版本叙事。日期为发布日。

## 未发布

仓库侧。

- **新增 `market/`：DSH 插件市场索引（format 1）。** 把插件挂进 Harness 的「发现」页，不必手动 `pnpm pack` + `dsh plugin add`。索引里的安装源是钉死在 commit 上的 git spec（`git+https://github.com/InsMyHe/dsh-qqark.git#<commit>&path:/dsh-plugin-qqark`）。
- **插件 `package.json` 补 `engines.dsh`**：`>=0.2.1-0 <0.3.0`，与市场索引里的 `engines.dsh` 保持一致。
- **新增 `scripts/check-market-index.mjs`**：逐条对齐官方 `@deepseek-ai/dsh-plugin-market-index` 的 format 1 规则（commit 必须 40 位小写十六进制、图标解析、category/label 必须已声明），CI 里同步执行。
- **修复 `selftest.py` 的两条偶发失败断言。**「已入下载队列」与「视频任务入队」原本走 `db.recent_tasks(10/20)` 的排序窗口，而 mock 演示文件的后台重试会不断刷新 `updated_at`，把刚入队那条挤出窗口，导致 Windows CI 上随机红。改为按文件名直查全表计数，与调度时序无关。
- **清理仓库内的个人信息。** 删掉 `docs/screenshots/` 三张真机界面截图（像素里含真实群名与文件名，且文件名把本地端口写进了名字），五份插件 README 的图片引用一并去掉；`tests/test_gone_state.py` 里两处调试时粘进去的真实文件名换成中性占位。`scripts/check_no_personal_paths.py` 新增一类检查：从本机 `data/qqark.db` 动态取群名/群号/文件名当词表扫描仓库，词表只在运行时存在、不落库，库不存在时自动跳过——这样以后写测试顺手粘真实数据会被 CI 直接拦下。

## 0.5.2

插件侧。

- **修复：列群文件必须显式传 `file_count`。** NapCat 的 `get_group_root_files` / `get_group_files_by_folder` 默认只回 50 条，内部翻页在 `r.length === n.fileCount` 时即退出。大群因此被静默截断，截断又级联成不可逆的 `gone` 误判。现在固定传 `LIST_FILE_COUNT = 1000`。
- **修复：`gone` 是终态，不再和「失败」混在一起。** 只有三条同时成立才判 `gone`——换不到新 `file_id`、扫描未报错、整棵树查无此名。目录只读了一半时任务保持可重试的 `failed`。
- 真机验证：`failed` 从 162 降到 0，74 条转入 `gone`，已完成的 146 条不受影响。

## 0.5.0

插件侧。

- 侧边栏面板排版改造：窄面板不再使用表格，改用堆叠行，420px 宽度下仍可读。
- 新增若干截图尺寸适配。

## 0.4.4

插件侧。

- **修复：先确认归档器真的起来了，再切断外部机器人。** 之前会先改 NapCat 客户端列表、再拉起归档器，归档器起不来时两边都不工作。现在顺序反过来，并且失败会回滚。

## 0.4.3

插件侧。

- **修复：启动子进程绝不能用 `detached: true`。** Windows 上 `detached: true` 的每一种组合都会让子进程在一秒内退出。改用 `cmd /c start`。

## 0.4.1

插件侧。

- **启动后一定回读验证**：轮询归档器的 `/api/status` 直到它真的应答，才向 UI 报成功。`spawn` 返回成功不等于进程活着。
- **快照被污染时自动找回**：归档模式切换前的客户端列表存在 `data/qqark-switch.json` 的 `grabClientsBefore`，若该文件被写坏，会从 NapCat 当前配置里反推一份可用快照。

## 0.4.0

插件侧。

- 概览页收敛成**一个启动入口**（原先有多个重复按钮，容易误点）。
- 「启动归档器」并入归档模式卡片。
- 日志页明确列出它读取哪些文件。

## 0.3.4

插件侧。

- **修复：面板报 HTTP 404。** 宿主路由必须带前导 `/`，而浏览器 `fetch` 必须不带——GUI 挂在 `<base href="./">` 下，绝对路径会绕过插件路由前缀（DSH issue #1707）。

## 0.3.0

插件侧。

- 新增**归档模式开关**：一键在「归档专用号」和「日常号」之间切换 NapCat 登录，并热重载 OB11 客户端列表（走 `OB11Config/SetConfig`）。

## 0.2.1

插件侧。

- 侧边栏可以直接拉起归档器进程（`启动.bat`），不再要求用户先自己开。

## 0.1.0

归档器首发。

- 连接 NapCat 反向 WebSocket，接收 `group_upload` 事件与聊天媒体消息。
- 群文件 / 聊天视频 / 聊天文件下载，`.part` 断点续传、`os.replace` 原子落盘、403/404 自动换链接重试。
- 按 `(群号, 文件名, 字节数)` 去重（不能用 `file_id`，NapCat 每次列举都会换一个新的）。
- 定时补扫：软件没开期间漏掉的群文件靠遍历目录找回。
- 本地媒体库 `http://127.0.0.1:7801`：搜索、筛选、HTTP Range 播放、缩略图。
- OneBot 客户端层写操作黑名单，`send_*` / `set_group_*` / `upload_group_file` 直接抛异常。
- 纯 Python 3.11 标准库，零第三方依赖。
