/**
 * dsh-plugin-qqark — QQ 群文件 / 视频归档器的 DSH 插件桥。
 *
 * 设计边界（刻意为之）：
 *   · 下载归档由独立的常驻进程完成（DSH 没开也不会漏录）。
 *     原因：归档需要 7×24 常驻，而 DSH 会话是按需启动的；把下载塞进插件会在
 *     DSH 关闭期间永久漏掉群文件与聊天视频（视频直链随 rkey 过期就基本救不回来）。
 *   · 插件只跟本机归档器的 HTTP API 说话，不碰 NapCat / OneBot。
 *   · 暴露给模型（agent）的四个工具全部只读；写操作（改配置、触发补扫、开关群）
 *     只由左侧边栏 UI 里用户亲手点击触发。
 */

import { spawn } from 'node:child_process'
import {
  closeSync,
  existsSync,
  openSync,
  readdirSync,
  readFileSync,
  readSync,
  statSync,
  writeFileSync,
} from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { defineTool } from '@deepseek-ai/dsh-tools'
import Schema from '@deepseek-ai/schemastery'

import {
  appendSwitchLog,
  clearCredentialCache,
  detectRunningKernel,
  findConfigDirs,
  findNapcatLauncher,
  getLoginInfo,
  hotSetClients,
  killQQ,
  launchNapCat,
  makeClient,
  portListening,
  readOnebotFile,
  readState,
  readWebuiConfig,
  restartNapCat,
  setQuickLoginQQ,
  switchLogFile,
  waitForLogin,
  waitPort,
  writeState,
} from './napcat.js'

export const name = 'dsh-plugin-qqark'

/** 只依赖工具注册表；webServer 走可选注入，缺失时插件仍能加载（只是没有 UI 后端）。 */
export const inject = ['tools']

export const Config = Schema.object({
  baseUrl: Schema.string()
    .default('http://127.0.0.1:7801')
    .description('本机 QQ 归档器的 Web 服务地址（设置页里「本地媒体库」的那个端口）。'),
  timeoutMs: Schema.natural()
    .default(8000)
    .description('单次 HTTP 请求超时（毫秒）。'),
  appDir: Schema.string()
    .default('')
    .description('归档器程序目录（含「启动.bat」）。留空则自动在常用位置里找。'),
  grabAccount: Schema.string()
    .default('')
    .description('归档专用的 QQ 号。开启归档开关后 NapCat 会登录这个号。留空则归档开关不可用，请填成你自己的号。'),
  quietAccount: Schema.string()
    .default('')
    .description('日常使用的 QQ 号。关闭归档开关后 NapCat 会切回这个号。留空则归档开关不可用，请填成你自己的号。'),
  napcatConfigDir: Schema.string()
    .default('')
    .description('NapCat 的 napcat/config 目录。留空则自动查找。'),
  wsPort: Schema.natural()
    .default(6200)
    .description('归档器接收 NapCat 反向 WS 的端口。'),
  astrbotPort: Schema.natural()
    .default(6199)
    .description('会被归档开关切断的那个端口（AstrBot 等外部机器人）。'),
})

const textOutput = {
  schema: { type: 'string' },
  render(_args, value) {
    return [{ type: 'text', text: String(value) }]
  },
}

// ---------- 传输层 ----------

class BridgeError extends Error {}

/**
 * 从 fetch 抛出的错误里挖出真正的错误码。
 * Node 18+ 的 undici 在连接被拒时只给 `TypeError: fetch failed`，
 * 真实码藏在 `err.cause` 里；而 `cause` 有时是 AggregateError，
 * 此时要下探 `cause.errors[]`（多个地址都失败会聚合成一个数组）。
 */
function errorCode(err) {
  const seen = new Set()
  const walk = (e, depth) => {
    if (!e || depth > 4 || seen.has(e)) return ''
    if (typeof e === 'object') seen.add(e)
    const own = e?.code
    if (typeof own === 'string' && own) return own
    for (const next of [e?.cause, ...(Array.isArray(e?.errors) ? e.errors : [])]) {
      const hit = walk(next, depth + 1)
      if (hit) return hit
    }
    return ''
  }
  return walk(err, 0)
}

/** 判断这次失败是不是"根本没连上"，而不是别的请求错误。 */
function isConnectFailure(code, err) {
  if (err?.name === 'TimeoutError') return true
  if (code === 'ECONNREFUSED' || code === 'ECONNRESET' || code === 'EHOSTUNREACH') return true
  if (code === 'ENOTFOUND' || code === 'EAI_AGAIN' || code === 'ETIMEDOUT') return true
  // 兜底：undici 把码埋得太深取不出来时，认这条固定文案。
  const msg = String(err?.message ?? '')
  if (msg === 'fetch failed' || /ECONNREFUSED|ECONNRESET/i.test(msg)) return true
  return false
}

/** 统一发一个请求并解析 JSON；所有网络故障都翻译成 BridgeError。 */
async function requestJson(config, path, init = {}) {
  const url = new URL(path, config.baseUrl)
  let res
  try {
    res = await fetch(url, {
      ...init,
      headers: { accept: 'application/json', ...(init.headers ?? {}) },
      signal: AbortSignal.timeout(config.timeoutMs),
    })
  } catch (err) {
    const code = errorCode(err)
    if (isConnectFailure(code, err)) {
      // 只用有信息量的描述：err.name 恒为 TypeError，写出来反而误导。
      const why = code || err?.cause?.message || err?.message || '连接失败'
      throw new BridgeError(
        `连不上 QQ 归档器（${config.baseUrl}）：${why}\n` +
          '请先在程序目录双击「启动.bat」把归档器跑起来，再重试。',
      )
    }
    throw new BridgeError(`请求 ${path} 失败：${err?.message || err}`)
  }
  if (!res.ok) throw new BridgeError(`请求 ${path} 返回 HTTP ${res.status} ${res.statusText}`)
  try {
    return await res.json()
  } catch {
    throw new BridgeError(`请求 ${path} 返回的不是合法 JSON`)
  }
}

function getJson(config, path) {
  return requestJson(config, path, { method: 'GET' })
}

function postJson(config, path, payload) {
  return requestJson(config, path, {
    method: 'POST',
    headers: { 'content-type': 'application/json; charset=utf-8' },
    body: JSON.stringify(payload ?? {}),
  })
}

/** 统一兜底：把可预期的桥接故障转成一句人话，而不是抛栈给模型。 */
function guarded(fn) {
  return async (...args) => {
    try {
      return await fn(...args)
    } catch (err) {
      if (err instanceof BridgeError) return err.message
      return `QQ 归档器插件出错：${err?.message || err}`
    }
  }
}

// ---------- 格式化 ----------

function fmtSize(bytes) {
  const n = Number(bytes) || 0
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = n / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`
}

function fmtTime(sec) {
  const n = Number(sec) || 0
  if (!n) return '-'
  const d = new Date(n * 1000)
  if (Number.isNaN(d.getTime())) return '-'
  const p = (x) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function oneLine(s, max = 60) {
  const t = String(s ?? '').replace(/\s+/g, ' ').trim()
  return t.length > max ? `${t.slice(0, max - 1)}…` : t
}

function table(headers, rows) {
  if (rows.length === 0) return '（没有数据）'
  const widths = headers.map((h, i) =>
    Math.max(strWidth(h), ...rows.map((r) => strWidth(String(r[i] ?? '')))),
  )
  const pad = (s, w) => {
    const d = w - strWidth(s)
    return d > 0 ? s + ' '.repeat(d) : s
  }
  const line = (cells) => cells.map((c, i) => pad(String(c ?? ''), widths[i])).join('  ')
  return [line(headers), line(widths.map((w) => '-'.repeat(w))), ...rows.map(line)].join('\n')
}

/** 中日韩字符按 2 个宽度算，避免中文表格错位。 */
function strWidth(s) {
  let w = 0
  for (const ch of String(s ?? '')) {
    const cp = ch.codePointAt(0)
    w +=
      (cp >= 0x1100 && cp <= 0x115f) ||
      (cp >= 0x2e80 && cp <= 0xa4cf) ||
      (cp >= 0xac00 && cp <= 0xd7a3) ||
      (cp >= 0xf900 && cp <= 0xfaff) ||
      (cp >= 0xfe30 && cp <= 0xfe6f) ||
      (cp >= 0xff00 && cp <= 0xff60) ||
      (cp >= 0xffe0 && cp <= 0xffe6)
        ? 2
        : 1
  }
  return w
}

// ---------- 左侧边栏 UI 的宿主侧后端 ----------
//
// 浏览器与 Python 归档器不同源，直接 fetch 会被 CORS 挡住；这里用宿主
// webServer 做同源反代。路径一律**不带前导斜杠**：GUI 用 <base href="./">，
// 根绝对路径会逃出子路径部署前缀（issue #1707）。

const ROUTE_NS = 'api/dsh-qqark'

const ROUTES = [
  'status',
  'files',
  'groups',
  'tasks',
  'config',
  'scan',
  'retry',
  'group-enable',
  'refresh-groups',
  'open-folder',
  'launcher',
  'launch',
  'switch-state',
  'switch',
  'logs',
  'open-log-dir',
]

/** 归档器目录的自动探测顺序（config.appDir 非空时优先用它）。 */
function appDirCandidates(config) {
  const home = os.homedir()
  const names = ['qq聊天文件视频抓取软件开发', 'qqark', 'QQ归档器']
  const roots = [path.join(home, 'Documents'), home, path.join(home, 'Desktop'), process.cwd()]
  const out = []
  const explicit = String(config?.appDir ?? '').trim()
  if (explicit) out.push(explicit)
  for (const root of roots) {
    for (const name of names) out.push(path.join(root, name))
  }
  return out
}

/** 定位程序目录与「启动.bat」。返回 `{appDir, batPath, batOk}`，全部失败时 appDir 为空串。 */
function resolveLauncher(config) {
  const explicit = String(config?.appDir ?? '').trim()
  // 用户显式填了 appDir 就只认它，找不到就直接报「缺启动.bat」。
  // 为什么不能回退去猜：猜到的往往是另一个（也许在别的盘的）真实程序目录，
  // 结果就是用户在 A 目录点启动、实际跑起来的是 B 目录的归档器——
  // 保存位置、数据库全都对不上，而且事后极难察觉。
  if (explicit) {
    const batPath = path.join(explicit, '启动.bat')
    if (existsSync(batPath)) return { appDir: explicit, batPath, batOk: true }
    return { appDir: explicit, batPath: '', batOk: false }
  }
  for (const dir of appDirCandidates(config)) {
    if (!dir) continue
    if (!existsSync(dir)) continue
    const batPath = path.join(dir, '启动.bat')
    if (existsSync(batPath)) return { appDir: dir, batPath, batOk: true }
  }
  // 退一步：只要目录存在就先认下来，让 UI 提示"缺 启动.bat"而不是整条功能消失。
  for (const dir of appDirCandidates(config)) {
    if (!dir) continue
    try {
      if (existsSync(dir) && readdirSync(dir).some((n) => n.toLowerCase().endsWith('.bat'))) {
        return { appDir: dir, batPath: '', batOk: false }
      }
    } catch {
      /* 目录不可读就跳过 */
    }
  }
  const first = appDirCandidates(config).find(Boolean) || ''
  return { appDir: first, batPath: '', batOk: false }
}

let lastLaunchAt = 0

/** 探测归档器是否在跑（复用 /api/status，超时短）。 */
async function probeRunning(config) {
  try {
    await getJson({ ...config, timeoutMs: Math.min(config.timeoutMs || 8000, 2500) }, '/api/status')
    return true
  } catch {
    return false
  }
}

/** 拿归档器的 /api/status；失败返回 null（用于区分「没起来」和「起来了但目录不可写」）。 */
async function readStatus(config) {
  try {
    return await getJson({ ...config, timeoutMs: Math.min(config.timeoutMs || 8000, 2500) }, '/api/status')
  } catch {
    return null
  }
}

/** 提权启动时 PowerShell 把结果写到这里，插件再回读——spawn 本身拿不到 UAC 结果。 */
export function launchLogFile() {
  return path.join(os.tmpdir(), 'qqark-launch.log')
}

/**
 * 启动「启动.bat」。
 *
 * `elevated` 为假（默认）时走 `cmd /c start`，直接以当前权限起一个可见窗口。
 * `elevated` 为真时先写一个 .ps1，再用 `cmd /c start` 把 PowerShell 拉起来，
 * 由它执行 `Start-Process -Verb RunAs` 弹 UAC。
 *
 * ⚠️ 两条路径都**绝对不能加 `detached: true`**（0.4.3 修）。
 * 实测：同样一段 PowerShell，父进程存活时
 *   - `spawn(..., {detached:true, stdio:'ignore'})` → PowerShell 连脚本第一行都没执行就退了
 *     （日志 0 字节、marker 不出现），而 spawn 本身不报错，
 *     于是插件只能干等 60 秒然后报「连不上」，用户看到的就是"报错 + 且未启动归档器"。
 *   - 去掉 detached 后立刻正常，1 秒就绪。
 * 不加 detached 也能让子进程活过父进程：`cmd /c start` 本身已经把 powershell
 * 交接给一个新进程，实测父进程 exit 后它照样跑完。
 *
 * 返回 `{ok, via, logFile, error}`：**ok 只代表命令成功发出，不代表归档器起来了**，
 * 真正的判据是靠 waitArchiver() 轮询 /api/status。
 */
function spawnLauncher(batPath, elevated) {
  const dir = path.dirname(batPath)
  const bat = path.basename(batPath)
  const logFile = launchLogFile()
  if (elevated) {
    try {
      writeFileSync(logFile, '')
    } catch {
      /* 写不进去不影响启动，只是少一条诊断线索 */
    }
    // 脚本先落盘成 .ps1 再交给 -File：这样完全绕开 Node→cmd 命令行的引号和
    // 换行转义，中文路径也不会被代码页搞坏。
    // 用 try/catch 包住 Start-Process：UAC 被点「否」时它会抛「操作已被用户取消」，
    // 写进日志后 waitArchiver 就能立刻判定失败，不用干等满 60 秒。
    const script = [
      `$ErrorActionPreference = 'Continue'`,
      `$log = ${psQuote(logFile)}`,
      `try {`,
      `  Start-Process -FilePath ${psQuote(path.join(dir, bat))} -WorkingDirectory ${psQuote(dir)} -Verb RunAs -ErrorAction Stop`,
      `  Add-Content -Path $log -Value ('SPAWNED ' + (Get-Date -Format o)) -Encoding UTF8`,
      `} catch {`,
      `  Add-Content -Path $log -Value ('FAILED ' + $_.Exception.Message) -Encoding UTF8`,
      `}`,
    ].join('\n')
    const ps1 = path.join(os.tmpdir(), 'qqark-launch.ps1')
    try {
      writeFileSync(ps1, script, 'utf8')
    } catch (err) {
      return { ok: false, logFile, error: `写启动脚本失败：${err?.message || err}` }
    }
    try {
      const child = spawn(
        'cmd.exe',
        ['/c', 'start', '', '/b', 'powershell.exe', '-NoProfile', '-NonInteractive',
          '-ExecutionPolicy', 'Bypass', '-File', ps1],
        { stdio: 'ignore', windowsHide: true },
      )
      child.unref()
    } catch (err) {
      return { ok: false, logFile, error: `调用 PowerShell 失败：${err?.message || err}` }
    }
    return { ok: true, via: 'elevated', logFile }
  }
  // 非提权：交给 cmd 起一个可见窗口，用户能看到运行日志，也方便关掉。
  // 注意这里**没有** detached —— 见上面的说明，它会杀死子进程。
  try {
    const child = spawn('cmd.exe', ['/c', 'start', '', bat], {
      cwd: dir,
      stdio: 'ignore',
      windowsHide: false,
    })
    child.unref()
  } catch (err) {
    return { ok: false, error: `调用 cmd 失败：${err?.message || err}` }
  }
  return { ok: true, via: 'cmd', logFile }
}

/** 读提权启动留下的诊断行，例如 `FAILED 操作已被用户取消。`。 */
export function readLaunchLog() {
  try {
    const file = launchLogFile()
    if (!existsSync(file)) return ''
    // Windows PowerShell 5.1 的 `Add-Content -Encoding UTF8` 会写 BOM，
    // 不剥掉的话 `startsWith('FAILED')` 永远为假，UAC 被拒时要空等满 60 秒。
    const text = readFileSync(file, 'utf8').replace(/^\uFEFF/, '').trim()
    // 只关心最后一次的结果（STARTED 之前可能残留旧行）
    const lines = text.split(/\r?\n/).filter(Boolean)
    return lines.length ? lines[lines.length - 1].replace(/^\uFEFF/, '') : ''
  } catch {
    return ''
  }
}

/**
 * 等归档器的 7801 起来。这是「开启」流程唯一可信的成功判据。
 *
 * 为什么不能用 spawn 的返回值：提权是一次 UAC 交接，PowerShell 立刻 return，
 * 真正起 Python 的是被提权的 cmd，插件进程既看不到它的退出码，也不该绑住它
 * （detached）。只有端口监听是跨进程、跨提权边界都成立的事实。
 */
async function waitArchiver(config, timeoutMs, onTick) {
  const deadline = Date.now() + timeoutMs
  let tick = 0
  while (Date.now() < deadline) {
    const status = await readStatus(config)
    if (status) {
      // 端口通了还要看保存目录写不写得进去：受限令牌下 /api/status 会回
      // save_root_ok=false，这时「归档器已就绪」也是半残，必须如实告诉用户。
      if (status.save_root_ok === false) {
        return {
          ok: false,
          error: `归档器起来了，但保存目录写不进去：${status.save_root || '(未配置)'}`,
        }
      }
      return { ok: true, status }
    }
    // 提权被拒时没必要干等满 60 秒
    const log = readLaunchLog()
    if (log.startsWith('FAILED')) {
      return { ok: false, error: `提权启动失败：${log.slice(7).trim() || 'UAC 被取消'}`, denied: true }
    }
    tick += 1
    if (onTick && tick % 6 === 0) onTick(Math.round((Date.now() - (deadline - timeoutMs)) / 1000))
    await new Promise((r) => setTimeout(r, 1200))
  }
  return { ok: false, error: `已发出启动命令，但 ${Math.round(timeoutMs / 1000)} 秒内 ${config.baseUrl} 一直没有响应。` }
}

/** PowerShell 单引号字符串转义：内部单引号写两遍。 */
function psQuote(s) {
  return `'${String(s).replace(/'/g, "''")}'`
}

// ---------- 日志读取 ----------

/**
 * 读日志尾部。为什么要自己解析而不是直接把文件丢给前端：
 * 归档器日志是 `时间 级别 线程 模块 内容` 的定宽文本，直接显示很难读；
 * 这里统一成 `{t, level, src, msg}`，前端就能按级别上色、按来源分组。
 */
function tailLines(file, limit) {
  if (!existsSync(file)) return []
  let text = ''
  try {
    const stat = statSync(file)
    const size = stat.size
    // 只读尾部，避免一条日志几 MB 时把整个文件读进内存
    const maxBytes = 512 * 1024
    if (size > maxBytes) {
      const fd = openSync(file, 'r')
      try {
        const buf = Buffer.alloc(maxBytes)
        readSync(fd, buf, 0, maxBytes, size - maxBytes)
        text = buf.toString('utf8')
        // 第一行大概率是被截断的半行，丢掉
        const nl = text.indexOf('\n')
        if (nl >= 0) text = text.slice(nl + 1)
      } finally {
        closeSync(fd)
      }
    } else {
      text = readFileSync(file, 'utf8')
    }
  } catch {
    return []
  }
  return text.split(/\r?\n/).filter(Boolean).slice(-limit)
}

const LOG_RE = /^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[,.]\d+)?)\s+(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL)\s+(\S+)\s+(\S+)\s+([\s\S]*)$/

function parseArchiverLine(line) {
  const m = LOG_RE.exec(line)
  if (!m) return { t: '', level: 'INFO', src: 'other', msg: line }
  const [, t, lvl, _thread, mod, msg] = m
  const level = lvl === 'WARNING' || lvl === 'WARN' ? 'WARN' : lvl
  // src 用模块名末段，UI 上更短
  const src = String(mod || '').replace(/^qqark\.?/, '') || 'core'
  return { t: t.replace(',', '.'), level, src, msg }
}

function parsePluginLine(line) {
  const m = /^(\S+)\s+(INFO|WARN|ERROR)\s+\[([^\]]+)\]\s*([\s\S]*)$/.exec(line)
  if (!m) return { t: '', level: 'INFO', src: 'plugin', msg: line }
  return { t: m[1], level: m[2], src: m[3], msg: m[4] }
}

function readLogs(config, kind, limit) {
  const launcher = resolveLauncher(config)
  const appDir = launcher.appDir || ''
  const max = Math.max(20, Math.min(Number(limit) || 300, 2000))
  const archiverFile = appDir ? path.join(appDir, 'data', 'qqark.log') : ''
  const pluginFile = appDir ? switchLogFile(appDir) : ''

  const out = { ok: true, appDir, archiverFile, pluginFile, archiver: [], plugin: [], errors: [] }

  if (kind !== 'plugin') {
    out.archiver = tailLines(archiverFile, max).map(parseArchiverLine)
  }
  if (kind !== 'archiver') {
    out.plugin = tailLines(pluginFile, max).map(parsePluginLine)
  }

  // 「只看问题」视图：两边所有 WARN/ERROR，按时间倒序合并
  const errs = []
  for (const e of out.archiver) if (e.level === 'ERROR' || e.level === 'WARN') errs.push({ ...e, from: '归档器' })
  for (const e of out.plugin) if (e.level === 'ERROR' || e.level === 'WARN') errs.push({ ...e, from: '插件' })
  errs.sort((a, b) => String(b.t).localeCompare(String(a.t)))
  out.errors = errs.slice(0, 200)

  // 文件大小，让 UI 能显示「日志有多大」
  out.sizes = {}
  for (const [k, f] of [['archiver', archiverFile], ['plugin', pluginFile]]) {
    try {
      out.sizes[k] = f && existsSync(f) ? statSync(f).size : 0
    } catch {
      out.sizes[k] = 0
    }
  }
  return out
}

function sendJson(res, status, body) {
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
  })
  res.end(JSON.stringify(body))
}

async function readBody(req) {
  const chunks = []
  for await (const chunk of req) chunks.push(chunk)
  const raw = Buffer.concat(chunks).toString('utf8')
  if (!raw.trim()) return {}
  try {
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    throw new BridgeError('请求体不是合法 JSON')
  }
}

/** 把一路查询参数原样转发给归档器（只放行已知键）。 */
function forwardQuery(url, allowed) {
  const qs = new URLSearchParams()
  for (const key of allowed) {
    const v = url.searchParams.get(key)
    if (v !== null && v !== '') qs.set(key, v)
  }
  const s = qs.toString()
  return s ? `?${s}` : ''
}

/**
 * 一个路由处理器。`fn` 返回要发给浏览器的 JSON。
 * HTTP 状态恒为 200：业务错误放在 {ok:false,error} 里，
 * 这样 UI 能渲染成人话而不是把异常扔进控制台。
 */
function handler(fn) {
  return async (req, res) => {
    try {
      const body = fn.length > 0 ? await readBody(req) : {}
      sendJson(res, 200, await fn(body))
    } catch (err) {
      const msg = err instanceof BridgeError ? err.message : String(err?.message || err)
      sendJson(res, 200, { ok: false, error: msg })
    }
  }
}

function buildHandlers(config) {
  return {
    // 概览：一次请求同时取状态与统计，少一次往返。
    status: handler(async () => {
      const [status, stats] = await Promise.all([
        getJson(config, '/api/status'),
        getJson(config, '/api/stats'),
      ])
      return { ok: true, baseUrl: config.baseUrl, status, stats }
    }),

    // 注意：files 不在这里，它要读 URL 查询串，在 registerRoutes 里单独注册。

    groups: handler(async () => {
      const data = await getJson(config, '/api/groups')
      return { ok: true, groups: Array.isArray(data.groups) ? data.groups : [] }
    }),

    tasks: handler(async () => {
      const data = await getJson(config, '/api/tasks')
      return { ok: true, ...data }
    }),

    config: handler(async (body) => {
      if (Object.keys(body).length === 0) {
        return { ok: true, config: await getJson(config, '/api/config') }
      }
      const saved = await postJson(config, '/api/config', body)
      return { ok: true, config: saved.config ?? null }
    }),

    scan: handler(async () => {
      await postJson(config, '/api/scan', {})
      return { ok: true, message: '补扫已触发，稍等几秒刷新。' }
    }),

    retry: handler(async () => {
      const r = await postJson(config, '/api/retry', {})
      return { ok: true, message: `已把 ${r.retried ?? 0} 个失败任务重新排入队列。` }
    }),

    'group-enable': handler(async (body) => {
      const groupId = String(body.group_id ?? '')
      if (!groupId) throw new BridgeError('缺少 group_id')
      await postJson(config, '/api/group_enable', { group_id: groupId, enabled: body.enabled !== false })
      return { ok: true }
    }),

    'refresh-groups': handler(async () => {
      const r = await postJson(config, '/api/refresh_groups', {})
      const n = Array.isArray(r.groups) ? r.groups.length : 0
      return { ok: true, message: `群列表已刷新，共 ${n} 个群。` }
    }),

    'open-folder': handler(async (body) => {
      const id = Number(body.id)
      if (!Number.isFinite(id) || id <= 0) throw new BridgeError('缺少文件 id')
      await postJson(config, '/api/open_folder', { id })
      return { ok: true }
    }),

    // 启动器状态：程序目录在哪、有没有 启动.bat、归档器在不在跑、保存目录写不写得进去。
    launcher: handler(async () => {
      const located = resolveLauncher(config)
      const status = await readStatus(config)
      const running = !!status
      return {
        ok: true,
        ...located,
        running,
        // 归档器在跑但目录不可写时，UI 要能把「在运行」和「能归档」区分开来。
        saveRootOk: status ? status.save_root_ok !== false : null,
        saveRoot: status ? String(status.save_root || '') : '',
        platform: process.platform,
      }
    }),

    // 启动归档器。elevated=true 时走 UAC 提权，否则普通窗口启动。
    launch: handler(async (body) => {
      const elevated = body.elevated === true
      const located = resolveLauncher(config)
      if (!located.batOk || !located.batPath) {
        throw new BridgeError(
          `没找到「启动.bat」。${
            located.appDir ? `已定位目录：${located.appDir}` : '也找不到归档器程序目录。'
          }\n请在插件设置里把 appDir 填成程序所在目录。`,
        )
      }
      // 「已在运行」不等于「能归档」：保存目录写不进去时（受限令牌、盘只读）
      // 也算没就绪，得让用户能重新拉一次，否则点这个按钮只会收到一句
      // 「已经在运行了」，而实际上一个文件也收不进来。
      const runningStatus = await readStatus(config)
      if (runningStatus && runningStatus.save_root_ok !== false) {
        return { ok: true, already: true, message: '归档器已经在运行了，不需要重复启动。' }
      }
      if (runningStatus && runningStatus.save_root_ok === false) {
        // 先把这个「活着但没用」的实例停掉，否则新起的实例会撞上单实例锁。
        await stopArchiver(config)
      }
      // 防连点：2 秒内重复点击只算一次。
      if (Date.now() - lastLaunchAt < 2000) {
        return { ok: true, message: '刚刚已经发过启动命令，等几秒再看状态。' }
      }
      lastLaunchAt = Date.now()
      const spawned = spawnLauncher(located.batPath, elevated)
      if (!spawned.ok) throw new BridgeError(spawned.error)
      // 和「开启」流程一样：光发出命令不算数，必须等到 7801 有响应才算启动成功。
      const waited = await waitArchiver(config, 60000)
      if (!waited.ok) {
        appendSwitchLog(located.appDir, { ok: false, kind: 'launch', detail: waited.error })
        // 只有真的走了提权，才该提 UAC；否则让用户去勾 UAC 是纯粹的误导。
        const hints = elevated
          ? '可能原因：① UAC 授权框被点「否」；② 保存目录不可写；③ 启动.bat 里没找到 Python。'
          : '可能原因：① 保存目录不可写（可以勾上「管理员启动」再试）；② 启动.bat 里没找到 Python；③ 6200/7801 端口被别的程序占着。'
        throw new BridgeError(`${waited.error}\n${hints}`)
      }
      appendSwitchLog(located.appDir, { ok: true, kind: 'launch', detail: '归档器已就绪（手动启动）' })
      return {
        ok: true,
        message: elevated
          ? '归档器已以管理员身份启动并就绪。'
          : '归档器已启动并就绪。',
      }
    }),

    // 归档模式当前是什么状态（读状态文件 + 现查 NapCat 登录号 + 探端口）。
    'switch-state': handler(async () => switchState(config)),

    // 拨动开关。body = { on: true|false }。
    //
    // 立刻返回 {started:true}，真正的切换在后台跑。原因见 switchJob 的注释：
    // 一次切换要 8~75 秒，同步等会撞上某一层的请求超时，前端就只剩一句
    // 没头没尾的「切换失败」。前端拿到 started 后轮询 /switch-state 看进度。
    switch: handler(async (body) => {
      const on = body.on === true
      if (switchJob.running) {
        return { ok: false, error: '上一次切换还没结束，等它跑完再点。', steps: switchJob.steps }
      }
      switchJob = { running: true, on, startedAt: Date.now(), finishedAt: 0, ok: null, error: '', steps: [] }
      const job = switchJob

      // 注意：这里绝对不能 await。故意让它在后台跑完，请求立即回包。
      applySwitch(config, on, job)
        .then((res) => {
          job.ok = res.ok === true
          job.error = res.ok ? '' : String(res.error || '切换失败')
        })
        .catch((err) => {
          job.ok = false
          job.error = String(err?.message || err)
        })
        .finally(() => {
          job.running = false
          job.finishedAt = Date.now()
          switching = false
        })

      return { ok: true, started: true, on, steps: [] }
    }),

    // 读日志。body = { kind: ''|'archiver'|'plugin', limit: number }
    //
    // kind 为空时两边都读；errors 是两边 WARN/ERROR 合并后的「只看问题」视图。
    logs: handler(async (body) => readLogs(config, body.kind || '', body.limit || 300)),

    // 在资源管理器里打开日志所在目录（data 目录）。
    'open-log-dir': handler(async () => {
      const located = resolveLauncher(config)
      const appDir = located.appDir || ''
      if (!appDir) throw new BridgeError('找不到归档器程序目录，无法打开日志目录。')
      const dir = path.join(appDir, 'data')
      const cmd = process.platform === 'win32' ? ['explorer.exe', [dir]] : ['xdg-open', [dir]]
      try {
        // 同样不用 detached：explorer.exe 只是把请求转交给已经在跑的资源管理器，
        // 自己立刻返回，不需要脱离父进程；而 detached 在这个项目里已经坑过一次
        // （见 spawnLauncher 注释），能不用就不用。
        const child = spawn(cmd[0], cmd[1], { stdio: 'ignore', windowsHide: false })
        child.unref()
      } catch (err) {
        throw new BridgeError(`打开日志目录失败：${String(err?.message || err)}`)
      }
      return { ok: true, dir }
    }),
  }
}

// ---------- 归档模式切换（开关 -> 切号 + 断外挂 + 起停归档器） ----------

/**
 * 这一步是整个功能的「大脑」。它按顺序做四件事，任一步失败就把已经改过的东西
 * 回滚（尤其是 onebot11 配置文件），避免用户被留在一个半改状态。
 *
 * 开启（on=true）：
 *   1. 把这个号的反向 WS 客户端**替成只有归档器那一条** —— 于是 AstrBot 那种
 *      「挂在 6199 上的外部机器人」物理上连不上这个号，就不可能代它说话。
 *   2. 停掉当前登录号那边可能还开着的 AstrBot 通道（只影响归档号自己的配置）。
 *   3. SetQuickLoginQQ(grabAccount) + RestartNapCat，让 NapCat 以归档号重登。
 *   4. 等真正登上来，再启动归档器（普通权限；写不进保存目录时提示用管理员）。
 *
 * 关闭（on=false）：反过来——恢复归档号原来的 onebot11 配置，
 *   把归档器关掉，SetQuickLoginQQ(quietAccount) + RestartNapCat 切回日常号。
 *
 * 为什么必须「替换」而不是「追加」：追加的话 6199 那条还在，AstrBot 照样能
 * 通过它给这个号下达发言指令，那就没法保证「不发信息」。
 */
let switching = false // 已废弃：改用 switchJob.running，仅为兼容保留占位

/**
 * 后台切换任务的进度。为什么要有它：
 *
 * 一次切换要「重启 NapCat → 等它真的登上来」，最长 60~75 秒。以前 /switch
 * 是同步等完整条流程才回包，浏览器就这么干等一个 HTTP 响应——撞上任何一层的
 * 请求超时（宿主 web server、反向代理、浏览器 fetch），前端拿到的就只有一句
 * 「切换失败」，连卡在哪一步都看不到。
 *
 * 所以改成：/switch 立刻返回 {started:true}，真正的工作在后台跑，进度写进
 * 这个对象；前端每秒轮询 /switch-state 读它，逐步显示每一步结果。
 */
let switchJob = {
  running: false,
  on: null,
  startedAt: 0,
  finishedAt: 0,
  ok: null,
  error: '',
  steps: [],
}

function pickConfigDir(config) {
  const explicit = String(config?.napcatConfigDir ?? '').trim()
  if (explicit) {
    if (existsSync(path.join(explicit, 'webui.json'))) return { ok: true, dir: explicit }
    return { ok: false, error: `配置里指定的 NapCat 配置目录不可用：${explicit}` }
  }
  const dirs = findConfigDirs()
  if (dirs.length === 0) {
    return { ok: false, error: '找不到 NapCat 的 napcat/config 目录。请在插件设置里填 napcatConfigDir。' }
  }
  return { ok: true, dirs, dir: dirs[0] }
}

/**
 * 看这份客户端列表是不是"已经只剩我们自己"了。
 *
 * 用来识别上一次开启异常中断留下的脏快照：如果当前配置里全是连归档器端口的
 * 条目（或者干脆是空的），那它不可能是用户的原始配置——真·原始配置里应该还
 * 有 AstrBot 那种外挂连着的 6199。
 *
 * 判据取两条，宁可漏判也不错判（错判会把用户的真实配置丢掉）：
 *   ① 列表为空 → 视为"没有可用快照"；
 *   ② 每一条都指向本机 + 归档器 wsPort。
 */
function looksLikeArchiverOnly(clients, config) {
  if (!Array.isArray(clients) || clients.length === 0) return true
  const port = Number(config?.wsPort) || 6200
  return clients.every((c) => {
    const url = String(c?.url ?? '')
    if (!url) return false
    try {
      const u = new URL(url)
      // 注意 IPv6 字面量：`new URL('ws://[::1]:6200/ws').hostname` 拿到的是
      // 带方括号的 `[::1]`，不剥掉就永远匹配不上。
      const host = u.hostname.replace(/^\[|\]$/g, '')
      const hostOk = host === '127.0.0.1' || host === 'localhost' || host === '::1' || host === '0.0.0.0'
      return hostOk && Number(u.port) === port
    } catch {
      return false
    }
  })
}

/** 关掉归档器（走我们新加的 /api/shutdown），等它真的下线。 */
async function stopArchiver(config) {
  try {
    await postJson({ ...config, timeoutMs: Math.min(config.timeoutMs || 8000, 3000) }, '/api/shutdown', {})
  } catch {
    /* 本来就没在跑，正常 */
  }
  const deadline = Date.now() + 12000
  while (Date.now() < deadline) {
    if (!(await probeRunning(config))) return { ok: true }
    await new Promise((r) => setTimeout(r, 500))
  }
  return { ok: false, error: '归档器没有在 12 秒内退出，可能卡在下载中。' }
}

/** 当前 NapCat 登录的是哪个号（连不上就返回 null）。 */
async function currentLogin(configDir) {
  try {
    const info = await getLoginInfo(configDir, 5000)
    return info.ok ? info.uin : null
  } catch {
    return null
  }
}

/**
 * 执行切换。返回 `{ok, steps:[{name, ok, detail}]}`，供 UI 逐条显示。
 *
 * 顺序很关键：**先把账号切过去，再改客户端列表**。
 * 因为 OB11Config/SetConfig 改的是「当前登录号」的那份配置——登错号就会去改
 * 另一个 QQ 号的文件。等真正登上来之后再 SetConfig，既能热重载、又能落盘到
 * 正确的 onebot11_<QQ号>.json。
 *
 * `switchJob.running` 是互斥锁：切号要重启 NapCat（十几秒），期间再点一次会
 * 拿到「正在切换」而不是并发改配置。
 */
async function applySwitch(config, on, job) {
  const steps = job ? job.steps : []
  const push = (name, ok, detail) => {
    steps.push({ name, ok, detail: String(detail ?? '') })
    return ok
  }

  try {
    const grabbed = pickConfigDir(config)
    if (!grabbed.ok) {
      return { ok: false, error: grabbed.error, steps: [{ name: '找 NapCat 配置', ok: false, detail: grabbed.error }] }
    }

    const configDir = grabbed.dir
    const grabUin = String(config.grabAccount ?? '').trim()
    const quietUin = String(config.quietAccount ?? '').trim()
    if (!grabUin || !quietUin) {
      return { ok: false, error: '插件设置里 grabAccount / quietAccount 不能为空。', steps }
    }
    const launcher = resolveLauncher(config)
    const appDir = launcher.appDir || ''
    push('找 NapCat 配置', true, configDir)

    /**
     * 确保 NapCat 本身在跑。没跑就试着拉起它的启动器，再等 6099 端口起来。
     *
     * 为什么必须放最前面：后面每一步（SetQuickLoginQQ / OB11Config / Restart）
     * 都是打 NapCat 的 WebUI，NapCat 没开时第一步就会 ECONNREFUSED。以前这里
     * 不探测，用户看到的就是一句裸的 `fetch failed`，完全不知道要先去开 NapCat。
     */
    const ensureNapCatRunning = async () => {
      const webui = readWebuiConfig(configDir)
      if (!webui.ok) throw new Error(webui.error)

      const where = `${webui.host}:${webui.port}`

      /**
       * 内核预检（0.5.2 加，load-bearing）。
       *
       * 只判断「在不在跑」是不够的：packetBackend 只认特定范围的 QQ 版本。
       * 如果 NapCat 正在跑、但加载的是系统装的新版 QQ，那么这里会直接说
       * 「已在运行」然后返回，后面换链接一律 retcode=1400 packetBackend 不可用，
       * 文件一个都下不下来。所以跑着的时候还要看它跑的是哪个内核。
       */
      const checkKernel = (label = '检查 QQ 内核') => {
        // 必须把端口传进去：多个 QQ 进程并存时（旧实例占 6099、新实例顺延到
        // 6100），只有「谁在监听 napcatPort」才能说明当前这个 NapCat 用的是什么内核
        const k = detectRunningKernel(webui.port)
        if (!k.ok) {
          push(label, true, `未判断出内核版本（${k.reason}），跳过这项检查`)
          return { verdict: 'unknown' }
        }
        if (k.bundled) {
          push(
            label,
            true,
            `QQ ${k.version}（包内自带内核，packetBackend 可用${k.mixed ? `；另有 ${k.running.length - 1} 个旧进程并存` : ''}）`,
          )
          return { verdict: 'bundled', kernel: k }
        }
        return { verdict: 'external', kernel: k }
      }

      if (await portListening(webui.port, webui.host, 900)) {
        const seen = checkKernel()
        if (seen.verdict !== 'external') {
          push('检查 NapCat', true, `已在运行（${where}）`)
          return
        }
        const found = findNapcatLauncher()
        if (!found.ok || found.kernel !== 'bundled') {
          // 想修也修不了：找不到自带内核的启动方式，只警告，别乱杀进程
          push(
            '检查 QQ 内核',
            false,
            `正在跑的是外部安装的 QQ ${seen.kernel.version}，NapCat 的 packetBackend 不支持这一版，` +
              '换文件链接会一直失败。找不到包内自带内核的启动方式，无法自动纠正。',
          )
          return
        }
        // 能修：杀掉 QQ，用 napcat.bat 重新拉起包内内核
        push(
          '检查 QQ 内核',
          false,
          `正在跑的是外部安装的 QQ ${seen.kernel.version}（占用 ${webui.port} 端口的那个进程），` +
            'NapCat 的 packetBackend 不支持它；改用包内自带的内核重启',
        )
        // 旧 NapCat 常常是提权跑的，普通权限杀不掉会静默失败 → 内部会自己升到 UAC
        push('结束旧的内核', true, '正在结束所有 QQ 进程（旧实例可能是管理员权限，会弹一次授权框）')
        const killed = killQQ()
        if (!killed.ok) {
          throw new Error(
            `${killed.error}\n` +
              '正在跑的 QQ 是外部安装的新版本，NapCat 的 packetBackend 不支持它，换文件链接一定失败。',
          )
        }
        push('结束旧的内核', true, killed.note || '旧的内核进程已全部退出')
        // 给 QQ 留点时间退干净，否则新进程会撞上单实例锁、把 6099 顺延成 6100
        await new Promise((r) => setTimeout(r, 3000))
      }

      const found = findNapcatLauncher()
      if (!found.ok) {
        throw new Error(
          `NapCat 没在运行（${where} 连不上），也没找到它的启动器。\n` +
            '请手动双击 NapCat 目录里的 napcat.bat 把 QQ 和 NapCat 开起来，再点开启。',
        )
      }
      const started = launchNapCat(found.file)
      if (!started.ok) throw new Error(started.error)
      push(
        '启动 NapCat',
        true,
        found.kernel === 'bundled'
          ? `用 ${path.basename(found.file)} 拉起自带 QQ 内核（不经注册表，不会挑到系统那版），等它就位…`
          : found.elevated
            ? '已调用 launcher.bat（如果弹出 UAC 请点「是」），等它就位…'
            : '正在拉起启动器，等它就位…',
      )
      const up = await waitPort(webui.port, webui.host, 75000)
      if (!up) {
        throw new Error(
          `已经调用 ${path.basename(found.file)} 拉起 NapCat，但 ${webui.port} 端口 75 秒内没起来。\n` +
            '可能的原因：① 弹出的 UAC 授权框没点「是」；② QQ 需要扫码登录；③ NapCat 窗口里有报错。\n' +
            '请看一眼 NapCat 的窗口，处理好再重试。',
        )
      }
      push('等待 NapCat 就位', true, `${where} 已监听`)

      // 起来之后再确认一次内核，别让"以为换好了、其实还是老的"混过去
      if (found.kernel === 'bundled') {
        const after = checkKernel('复核 QQ 内核')
        if (after.verdict === 'external') {
          // 已经起来了但内核还是外面的那版：不拦流程（文件能列，只是换链接会失败），
          // 但必须留下一条看得见的警告，否则用户只会看到满屏 retcode=1400
          push(
            '复核 QQ 内核',
            false,
            `NapCat 起来了，但它加载的仍是外部安装的 QQ ${after.kernel.version}，` +
              'packetBackend 不支持这一版，下载会一直失败。也可能有旧的 NapCat 实例还占着端口。',
          )
        }
      }
    }

    /** 确保 NapCat 登的就是 targetUin；已经对了就不重启。 */
    const ensureLogin = async (targetUin) => {
      const setQ = await setQuickLoginQQ(configDir, targetUin)
      if (!setQ.ok) throw new Error(setQ.error)
      push('设置自动登录号', true, targetUin)

      const before = await currentLogin(configDir)
      if (before === targetUin) {
        push('切换账号', true, `已经是 ${targetUin}，无需重启`)
        return
      }
      const rs = await restartNapCat(configDir)
      if (!rs.ok) throw new Error(rs.error)
      push('重启 NapCat', true, `从 ${before || '未登录'} 切到 ${targetUin}`)
      // 重启会作废 Credential，等新进程就位后必须重新登录一次，否则后续
      // 每个 WebUI 接口都会拿到「未授权」，且容易一路撞到登录限流。
      clearCredentialCache()
      const ok = await waitForLogin(configDir, targetUin, 60000)
      if (!ok.ok) throw new Error(ok.error)
      push('等待登录', true, `${targetUin}（${ok.nick || '已上线'}）`)
    }

    if (on) {
      // ---- 开启：切到归档号，然后把它的客户端热改成「只连归档器」 ----
      await ensureNapCatRunning()
      await ensureLogin(grabUin)

      // 记住归档号原有的客户端，关闭时好还原（它平时可能本该连 6199）。
      //
      // 这里有个坑：如果上一次开启是「异常退出」（归档器没起来、或用户直接关掉
      // DSH），客户端列表会停在「只有归档器」的状态而我们又写下了 on:true。
      // 这时再点开启，拍到的「原有配置」就是那堆只剩归档器的脏数据，关闭时
      // 照着它"还原"等于什么都没还原——AstrBot 通道再也回不来了。
      //
      // 所以：拍快照前先看一眼，如果当前列表长得就像"只连我们自己"，说明这不是
      // 真·原始配置，改用状态文件里上次存的那份；两份都没有才只能认命。
      const snapFile = readOnebotFile(configDir, grabUin)
      const snapClients = Array.isArray(snapFile?.data?.network?.websocketClients)
        ? snapFile.data.network.websocketClients
        : []
      const prevSaved = appDir ? readState(appDir) : {}
      const prevClients =
        prevSaved && Array.isArray(prevSaved.grabClientsBefore) && prevSaved.grabUin === grabUin
          ? prevSaved.grabClientsBefore
          : null
      const snapshot = looksLikeArchiverOnly(snapClients, config)
        ? prevClients && !looksLikeArchiverOnly(prevClients, config)
          ? prevClients
          : null
        : snapClients

      // 走管理员权限启动：归档器要写 C:\Users\...\课程 这类工作区之外的目录，
      // 受限令牌下会直接 PermissionError。UAC 由用户点「是」放行。
      //
      // 注意：这里**必须轮询验证 7801 真的起来了**才算成功。以前只 spawn 一下就
      // push(true)，UAC 点了「否」或 Python 崩掉时状态机照样写 on:true，用户看到
      // 的是「开启成功」+「连不上归档器」两个自相矛盾的提示。
      const running = await probeRunning(config)
      let archiverUp = running
      if (running) {
        // 已经在跑也要看保存目录能不能写：受限令牌下 /api/status 会回
        // save_root_ok=false，「在运行」不等于「能归档」。
        const st = await readStatus(config)
        if (st && st.save_root_ok === false) {
          archiverUp = false
          push('启动归档器', false, `已经在运行，但保存目录写不进去：${st.save_root || '(未配置)'}`)
        } else {
          push('启动归档器', true, '已经在运行（无需提权）')
        }
      } else if (launcher.batOk && launcher.batPath) {
        // 默认**不提权**启动。为什么改了（0.4.3）：
        // 非提权实测 1 秒就绪、save_root_ok=true，而走 UAC 时确认框会弹在
        // 「安全桌面」上（PromptOnSecureDesktop=1），用户常常根本看不到，
        // PowerShell 就一直挂在 Start-Process 上，表现为点了开启毫无反应、
        // 最后只报「连不上」。所以只在**确实写不进保存目录**时才升级为提权。
        const spawned = spawnLauncher(launcher.batPath, false)
        if (!spawned.ok) {
          appendSwitchLog(appDir, { ok: false, kind: 'launch', detail: spawned.error })
          push('启动归档器', false, spawned.error)
        } else {
          push('启动归档器', true, '正在启动归档器…')
          push('等待归档器就绪', true, `等 ${config.baseUrl} 有响应…`)
          let waited = await waitArchiver(config, 60000)
          let triedElevated = false
          // 权限不足的典型症状是「端口通了但保存目录写不进去」。这时才值得
          // 付出一次 UAC 的代价：先停掉这个写不进去的实例，再用管理员身份重起。
          if (!waited.ok && /保存目录写不进去/.test(String(waited.error || ''))) {
            await stopArchiver(config)
            push('改用管理员重试', true, '普通权限写不进保存目录，正在弹 UAC 重试…')
            const elevated = spawnLauncher(launcher.batPath, true)
            if (!elevated.ok) {
              appendSwitchLog(appDir, { ok: false, kind: 'launch', detail: elevated.error })
              waited = { ok: false, error: elevated.error }
            } else {
              triedElevated = true
              waited = await waitArchiver(config, 60000)
            }
          }
          if (waited.ok) {
            archiverUp = true
            const st = waited.status || {}
            const detail = `归档器已就绪（保存目录 ${st.save_root || '可写'}）`
            steps[steps.length - 1] = { name: '等待归档器就绪', ok: true, detail }
            appendSwitchLog(appDir, { ok: true, kind: 'launch', detail })
          } else {
            const hints = triedElevated
              ? '可能原因：① UAC 授权框被点「否」；② 保存目录不可写；③ 启动.bat 里没找到 Python。'
              : '可能原因：① 保存目录不可写；② 启动.bat 里没找到 Python；③ 6200/7801 端口被别的程序占着。'
            const detail = `${waited.error}\n${hints}`
            steps[steps.length - 1] = { name: '等待归档器就绪', ok: false, detail }
            appendSwitchLog(appDir, { ok: false, kind: 'launch', detail: waited.error })
            // 这次是我们亲手拉起来又没就绪的实例，留在后台只会占着 6200/7801，
            // 下次点开启反而撞端口。清掉它，让「失败」是干净的失败。
            await stopArchiver(config)
          }
        }
      } else {
        push('启动归档器', false, '没找到「启动.bat」，请手动启动归档器')
      }

      // 归档器没就绪就不写 on:true——状态文件必须反映事实，否则下次关闭时
      // 会照着「其实没生效」的快照去还原账号，越还原越乱。
      if (!archiverUp) {
        return {
          ok: false,
          on: false,
          error: 'NapCat 已切到归档号，但归档器没能启动，所以没有进入归档模式。请按上面的原因处理后重试。',
          steps,
        }
      }

      // 顺序很重要（0.4.4 修）：**先确认归档器真能起来，再切断外部机器人**。
      //
      // 以前这一步排在启动验证之前，于是「归档器起不来」时会出现最难排查的那种
      // 状态：NapCat 那边归档号已经被改成只连 6200，但 6200 上根本没人在听，
      // 归档器也没有，而 switch.json 写着 on:false。等于这个号被吊在半空——
      // AstrBot 的通道没了、文件也收不了，面板却显示"没开启"。
      const hot = await hotSetClients(configDir, [makeClient('qqark', `ws://127.0.0.1:${config.wsPort}/ws`)])
      if (!hot.ok) throw new Error(hot.error)
      push('切断外部机器人', true, `现在只连归档器 ${config.wsPort}，AstrBot 那种外挂已失去通道`)

      // grabClientsBefore 存的是 snapshot（可能来自上次的状态文件，见上面拍快照那段），
      // 不是 snapClients —— 直接存当前列表会把「只有归档器」的脏数据当成原始配置。
      // 没有可信快照时存 null，关闭时就能如实说「没有上次的快照」而不是假还原。
      writeState(appDir, {
        on: true,
        grabUin,
        quietUin,
        configDir,
        grabClientsBefore: snapshot,
        at: new Date().toISOString(),
      })

      // 快照丢了要主动告诉用户，别让他在关闭时才发现通道回不来。
      if (!snapshot) {
        push(
          '记录原始配置',
          false,
          `归档号 ${grabUin} 现在的客户端列表已经是「只连归档器」了，拿不到它原本的配置。` +
            '关闭时无法自动还原外部机器人通道，需要手动改回。',
        )
      } else {
        push('记录原始配置', true, `已保存 ${snapshot.length} 条，关闭时用于还原`)
      }
      return { ok: true, on: true, grabUin, steps }
    }

    // ---- 关闭：先停归档器，再还原归档号配置，最后切回日常号 ----
    const stop = await stopArchiver(config)
    push('关闭归档器', stop.ok, stop.ok ? '已退出' : stop.error)

    await ensureNapCatRunning()

    const saved = appDir ? readState(appDir) : {}
    const beforeClients =
      saved && Array.isArray(saved.grabClientsBefore) && saved.grabUin === grabUin ? saved.grabClientsBefore : null

    // 此时登录的通常还是归档号，用 SetConfig 热还原最干净（会同时落盘到它的文件）
    if (Array.isArray(beforeClients)) {
      const hot = await hotSetClients(configDir, beforeClients)
      if (hot.ok) {
        push('还原外部机器人', true, `归档号 ${grabUin} 的客户端已还原成 ${beforeClients.length} 条`)
      } else {
        push('还原外部机器人', false, hot.error)
      }
    } else {
      push('还原外部机器人', true, '没有上次的快照，归档号的配置保持当前内容')
    }

    await ensureLogin(quietUin)

    writeState(appDir, { on: false, at: new Date().toISOString() })
    return { ok: true, on: false, quietUin, steps }
  } catch (err) {
    return { ok: false, error: String(err?.message || err), steps }
  }
}

/** 读切换状态，给 UI 显示「现在是归档模式吗」。 */
async function switchState(config) {
  const launcher = resolveLauncher(config)
  const grabbed = pickConfigDir(config)
  const state = launcher.appDir ? readState(launcher.appDir) : {}
  const out = {
    ok: true,
    mode: state?.on === true ? 'archive' : 'normal',
    switchAt: state?.at ?? '',
    grabAccount: String(config.grabAccount ?? ''),
    quietAccount: String(config.quietAccount ?? ''),
    wsPort: config.wsPort,
    astrbotPort: config.astrbotPort,
    configDir: grabbed.ok ? grabbed.dir : '',
    configDirOk: grabbed.ok,
    switching: switchJob.running,
    // 后台切换任务的进度。前端靠轮询这个字段逐步显示每一步结果。
    job: {
      running: switchJob.running,
      on: switchJob.on,
      startedAt: switchJob.startedAt,
      finishedAt: switchJob.finishedAt,
      ok: switchJob.ok,
      error: switchJob.error,
      steps: switchJob.steps.slice(),
      elapsedMs: switchJob.startedAt
        ? (switchJob.finishedAt || Date.now()) - switchJob.startedAt
        : 0,
    },
  }
  if (grabbed.ok) {
    try {
      const info = await getLoginInfo(grabbed.dir, 4000)
      out.currentUin = info.ok ? info.uin : ''
      out.currentNick = info.ok ? info.nick : ''
      out.napcatOnline = info.ok && info.online
    } catch {
      out.currentUin = ''
      out.currentNick = ''
      out.napcatOnline = false
    }
    // 归档号配置里还有没有通往外部机器人的客户端——这正是「会不会说话」的直接判据
    const grabFile = readOnebotFile(grabbed.dir, out.grabAccount)
    const clients = Array.isArray(grabFile?.data?.network?.websocketClients)
      ? grabFile.data.network.websocketClients
      : []
    out.grabClients = clients.map((c) => ({ name: String(c.name ?? ''), url: String(c.url ?? ''), enable: c.enable !== false }))
    out.grabHasExternal = clients.some(
      (c) => c.enable !== false && !String(c.url ?? '').includes(`:${config.wsPort}/`),
    )
  }
  out.archiverRunning = await probeRunning(config)
  out.astrbotListening = await portListening(config.astrbotPort)
  return out
}

/**
 * 注册全部路由。返回一个释放函数（红线：注册即 effect）。
 * `/files` 需要读 URL 查询串，单独处理。
 */
function registerRoutes(server, config) {  const handlers = buildHandlers(config)
  const disposers = []

  for (const route of ROUTES) {
    // ⚠️ 前导斜杠不能省。宿主 webserver 用 `new URL(req.url, "http://x").pathname`
    // 取路径，得到的一定是 `/api/dsh-qqark/status` 这种带前导斜杠的形式，然后
    // 拿它去 Map 里做严格字符串相等匹配（exact 路由）。这里少写斜杠就永远匹配
    // 不上，请求全部落到 SPA 兜底处理器，浏览器看到的正是「HTTP 404」。
    // 注意客户端反过来：GUI 用 <base href="./">，fetch 必须用无斜杠的 `api/...`。
    const path = `/${ROUTE_NS}/${route}`
    let fn = handlers[route]
    if (route === 'files') {
      const allowed = ['q', 'group_id', 'category', 'source', 'sort', 'limit', 'offset', 'media_only']
      fn = async (req, res) => {
        try {
          const url = new URL(req.url ?? '/', 'http://127.0.0.1')
          const data = await getJson(config, `/api/files${forwardQuery(url, allowed)}`)
          sendJson(res, 200, { ok: true, ...data })
        } catch (err) {
          const msg = err instanceof BridgeError ? err.message : String(err?.message || err)
          sendJson(res, 200, { ok: false, error: msg })
        }
      }
    }
    if (route === 'logs') {
      // logs 是 GET（前端拿 ?limit= 过来），body 永远是空的，所以参数从 URL 读。
      fn = async (req, res) => {
        try {
          const url = new URL(req.url ?? '/', 'http://127.0.0.1')
          const kind = url.searchParams.get('kind') || ''
          const limit = Number(url.searchParams.get('limit')) || 300
          sendJson(res, 200, readLogs(config, kind, limit))
        } catch (err) {
          const msg = err instanceof BridgeError ? err.message : String(err?.message || err)
          sendJson(res, 200, { ok: false, error: msg })
        }
      }
    }
    const off = server.register({ kind: 'exact', path, handler: fn })
    if (typeof off === 'function') disposers.push(off)
  }

  return () => {
    for (const off of disposers.splice(0)) {
      try {
        off()
      } catch {
        /* 卸载阶段各路由互相独立，单个失败不影响其余 */
      }
    }
  }
}

// ---------- 插件主体 ----------

export function apply(ctx, config) {
  // ---- 左侧边栏 UI 的后端路由（可选服务：没有 webServer 就安静跳过）----
  if (typeof ctx.inject === 'function') {
    ctx.inject(['webServer'], (sctx) => {
      const server = sctx.webServer
      if (!server || typeof server.register !== 'function') return
      const label = 'dsh-plugin-qqark: 左侧边栏 UI 路由'
      if (typeof sctx.effect === 'function') {
        sctx.effect(() => registerRoutes(server, config), label)
      } else {
        registerRoutes(server, config)
      }
    })
  }

  // ---- 四个只读工具 ----

  ctx.tools.register(
    defineTool({
      name: 'qqark_status',
      description:
        '查看本机 QQ 群文件归档器的运行状态：是否已连上 NapCat（OneBot）、当前登录账号、保存目录是否可写、下载队列与累计统计。排查「为什么没下载到东西」时先看这个。',
      parameters: {},
      output: textOutput,
      execute: guarded(async () => {
        const [status, stats] = await Promise.all([
          getJson(config, '/api/status'),
          getJson(config, '/api/stats'),
        ])
        const connected = status.onebot_connected ? '已连接' : '未连接'
        const accounts = Array.isArray(status.accounts) ? status.accounts : []
        const accLines =
          accounts.length > 0
            ? accounts
                .map(
                  (a) =>
                    `  · ${a.self_id}${a.nickname ? `（${a.nickname}）` : ''} ${a.alive ? '在线' : '离线'} ${a.addr ?? ''}`,
                )
                .join('\n')
            : '  · （暂无）'
        const dl = stats.downloader ?? {}
        const c = status.counters ?? {}

        return [
          `QQ 归档器：${status.save_root_ok ? '运行正常' : '在运行，但保存目录不可写'}`,
          `  OneBot / NapCat：${connected}（${status.account_count ?? accounts.length} 个账号）`,
          accLines,
          `  保存目录：${status.save_root ?? '-'}${status.save_root_ok ? '' : '   ← 不可写，下载会失败'}`,
          `  端口：WS ${status.ws_port ?? '-'} / Web ${status.web_port ?? '-'}`,
          '',
          `下载队列：进行中 ${dl.active ?? 0}，已完成 ${dl.done ?? 0}，失败 ${dl.failed ?? 0}，待下 ${dl.pending ?? 0}`,
          `累计入库：${stats.files ?? 0} 个文件，${fmtSize(stats.total_size ?? 0)}`,
          `实时事件：群文件 ${c.group_upload ?? 0}，聊天视频 ${c.chat_video ?? 0}，聊天文件 ${c.chat_file ?? 0}`,
          status.save_root_ok ? '' : '\n⚠️ 保存目录不可写：若归档器是从 IDE/受限终端启动的，请改用它自己的「启动.bat」双击运行。',
        ]
          .filter((x) => x !== '')
          .join('\n')
      }),
    }),
  )

  ctx.tools.register(
    defineTool({
      name: 'qqark_groups',
      description:
        '列出归档器已知的全部 QQ 群及其抓取开关状态（只看不下载）。用于确认某个群是否在监听名单里。',
      parameters: {},
      output: textOutput,
      execute: guarded(async () => {
        const data = await getJson(config, '/api/groups')
        const groups = Array.isArray(data.groups) ? data.groups : []
        if (groups.length === 0) {
          return '归档器还没有任何群。请先在归档器的网页里点「刷新群列表」，或在设置里填 watch_groups。'
        }
        const on = groups.filter((g) => g.enabled).length
        const rows = groups.map((g) => [
          g.enabled ? '抓取' : '停用',
          String(g.group_id ?? ''),
          oneLine(g.group_name ?? '', 30),
          String(g.member_count ?? '-'),
          g.last_scan ? fmtTime(g.last_scan) : '未扫',
          String(g.account ?? '') || '-',
        ])
        return [
          `共 ${groups.length} 个群，其中 ${on} 个在抓取。`,
          '',
          table(['状态', '群号', '群名', '人数', '上次扫描', '账号'], rows),
        ].join('\n')
      }),
    }),
  )

  ctx.tools.register(
    defineTool({
      name: 'qqark_files',
      description:
        '检索归档器已经下载到本地的群文件与聊天视频。支持按关键词、群号、类型过滤。返回文件名、大小、分类、所属群与本地路径，并给出可直接在浏览器打开的媒体链接 id。',
      parameters: {
        q: { type: 'string', description: '文件名关键词（模糊匹配）。' },
        group_id: { type: 'string', description: '只看这个群号。' },
        category: {
          type: 'string',
          description: '只看某一类：视频 / 文档 / 表格 / 幻灯片 / 压缩包 / 图片 / 音频 / 软件 / 代码 / 其他。',
        },
        source: { type: 'string', description: '来源过滤：群文件 / 聊天视频 / 聊天文件。' },
        limit: { type: 'number', description: '最多返回多少条，默认 20，上限 100。' },
        offset: { type: 'number', description: '跳过多少条，用于翻页，默认 0。' },
      },
      output: textOutput,
      execute: guarded(async (args) => {
        const limit = Math.min(Math.max(Number(args.limit) || 20, 1), 100)
        const offset = Math.max(Number(args.offset) || 0, 0)
        const qs = new URLSearchParams({ limit: String(limit), offset: String(offset), sort: 'new' })
        for (const key of ['q', 'group_id', 'category', 'source']) {
          const v = args[key]
          if (v !== undefined && v !== null && String(v).trim() !== '') qs.set(key, String(v).trim())
        }
        const data = await getJson(config, `/api/files?${qs.toString()}`)
        const files = Array.isArray(data.files) ? data.files : []
        const total = data.total ?? files.length
        if (files.length === 0) {
          return total === 0 ? '没有匹配的文件（归档器里还没有下载记录）。' : `第 ${offset + 1} 条起没有更多了，共 ${total} 条。`
        }
        const rows = files.map((f) => [
          String(f.id ?? ''),
          f.category ?? '-',
          oneLine(f.file_name ?? '', 42),
          fmtSize(f.size),
          oneLine(f.group_name ?? String(f.group_id ?? ''), 20),
          f.source ?? '-',
        ])
        const head = [
          `匹配到 ${total} 条，本页 ${files.length} 条（offset=${offset}）。`,
          '',
          table(['id', '类别', '文件名', '大小', '所属群', '来源'], rows),
        ]
        const media = files.filter((f) => ['视频', '音频', '图片'].includes(f.category)).slice(0, 5)
        if (media.length > 0) {
          head.push('', '媒体文件可在浏览器直接预览/播放：')
          for (const f of media) {
            head.push(`  ${oneLine(f.file_name ?? '', 40)} → ${config.baseUrl}/media/${f.id}`)
          }
        }
        if (total > offset + files.length) {
          head.push('', `还有 ${total - offset - files.length} 条，用 offset=${offset + files.length} 继续翻。`)
        }
        return head.join('\n')
      }),
    }),
  )

  ctx.tools.register(
    defineTool({
      name: 'qqark_tasks',
      description:
        '查看归档器的下载任务队列与最近任务明细：正在下、失败的、以及失败原因。用于排查「某个文件没下下来」。',
      parameters: {
        limit: { type: 'number', description: '最近任务条数，默认 15，上限 50。' },
      },
      output: textOutput,
      execute: guarded(async (args) => {
        const limit = Math.min(Math.max(Number(args.limit) || 15, 1), 50)
        const data = await getJson(config, '/api/tasks')
        const dl = data.downloader ?? {}
        const sc = data.scanner ?? {}
        const tasks = Array.isArray(data.tasks) ? data.tasks.slice(0, limit) : []

        const out = [
          `下载器：进行中 ${dl.active ?? 0}，已完成 ${dl.done ?? 0}，失败 ${dl.failed ?? 0}，待下 ${dl.pending ?? 0}`,
          `补扫器：${sc.running ? '运行中' : '空闲'}，已扫 ${sc.scanned ?? 0} 个群目录，入队 ${sc.queued ?? 0}，上次 ${sc.last_run ? fmtTime(sc.last_run) : '未运行'}`,
        ]
        if (sc.error) out.push(`补扫异常：${oneLine(sc.error, 200)}`)

        if (tasks.length === 0) {
          out.push('', '（还没有任何任务记录）')
          return out.join('\n')
        }
        const rows = tasks.map((t) => [
          t.state ?? '-',
          oneLine(t.file_name ?? '', 38),
          fmtSize(t.size),
          oneLine(t.group_name ?? String(t.group_id ?? ''), 18),
          String(t.attempts ?? 0),
        ])
        out.push('', table(['状态', '文件名', '大小', '所属群', '重试'], rows))

        const failed = tasks.filter((t) => t.state === 'failed' && t.last_error)
        if (failed.length > 0) {
          out.push('', '失败原因：')
          for (const t of failed.slice(0, 5)) {
            out.push(`  · ${oneLine(t.file_name ?? '', 36)} → ${oneLine(t.last_error, 160)}`)
          }
        }
        return out.join('\n')
      }),
    }),
  )
}
