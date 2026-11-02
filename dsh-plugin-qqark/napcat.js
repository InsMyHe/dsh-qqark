/**
 * NapCat 控制通道（宿主侧）。
 *
 * 只做三件事，全部通过 NapCat 自己的 WebUI HTTP 接口完成，不碰 QQ 进程内部：
 *   1. 找到 napcat/config 目录（下面就是 onebot11_<QQ号>.json）
 *   2. 用 WebUI token 登录拿 Credential，之后调 QQLogin/* 接口
 *   3. 改 onebot11_<QQ号>.json 里的 websocketClients，控制这个 QQ 号连谁
 *
 * 为什么不直接改 NapCat 的数据库或进程：那是注入态，改错就登不上。
 * onebot11_<QQ号>.json 是 NapCat 登录该号时读取的纯文本配置，改它是官方支持面。
 *
 * 安全边界：本模块的唯一写操作是「把这个 QQ 号的反向 WS 客户端列表设成指定内容」，
 * 以及调 RestartNapCat 让它重登。它不发消息、不点同意、不改任何权限。
 */

import { execFileSync, spawn } from 'node:child_process'
import crypto from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

/** napcat/config 在 NapCat 安装目录里的相对位置。 */
const CONFIG_SUBPATH = path.join('resources', 'app', 'napcat', 'config')

/** 找 NapCat 时扫的根目录，和 Python 端 qqark/napcat.py 的 SEARCH_ROOTS 对齐。 */
function searchRoots(extra) {
  const home = os.homedir()
  const roots = [
    extra,
    path.join(home, 'Downloads'),
    path.join(home, 'Desktop'),
    path.join(home, 'Documents'),
    'C:/NapCat',
    'C:/Program Files/NapCat',
    'D:/NapCat',
    'D:/',
  ]
  return roots.filter((r) => typeof r === 'string' && r.trim().length > 0)
}

/** 有界深度找目录，避免扫整个磁盘卡住。 */
function walkForConfig(root, maxDepth, out, seen) {
  if (out.length >= 6) return
  let real
  try {
    real = fs.realpathSync(root)
  } catch {
    return
  }
  if (seen.has(real)) return
  seen.add(real)

  const stack = [[root, 0]]
  while (stack.length > 0) {
    const [dir, depth] = stack.pop()
    if (depth > maxDepth) continue
    let entries
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true })
    } catch {
      continue
    }
    for (const entry of entries) {
      if (!entry.isDirectory()) continue
      const child = path.join(dir, entry.name)
      const candidate = path.join(child, CONFIG_SUBPATH)
      try {
        if (fs.statSync(candidate).isDirectory()) {
          out.push(candidate)
          if (out.length >= 6) return
        }
      } catch {
        /* 不是配置目录，继续下潜 */
      }
      // 跳过明显无关的超大目录
      if (entry.name === 'node_modules' || entry.name === '$Recycle.Bin' || entry.name === 'Windows') continue
      stack.push([child, depth + 1])
    }
  }
}

/** 返回所有 napcat/config 目录（已去重）。 */
export function findConfigDirs(extraRoot) {
  const found = []
  const seen = new Set()
  for (const root of searchRoots(extraRoot)) {
    try {
      if (!fs.statSync(root).isDirectory()) continue
    } catch {
      continue
    }
    walkForConfig(root, 4, found, seen)
    if (found.length >= 6) break
  }
  // 去重
  const uniq = []
  const uniqSeen = new Set()
  for (const dir of found) {
    let real
    try {
      real = fs.realpathSync(dir)
    } catch {
      real = dir
    }
    if (uniqSeen.has(real)) continue
    uniqSeen.add(real)
    uniq.push(dir)
  }
  return uniq
}

/** 读 webui.json，拿到端口与 token。 */
export function readWebuiConfig(configDir) {
  const file = path.join(configDir, 'webui.json')
  let raw
  try {
    raw = fs.readFileSync(file, 'utf8')
  } catch {
    return { ok: false, error: `读不到 ${file}` }
  }
  let data
  try {
    data = JSON.parse(raw)
  } catch {
    return { ok: false, error: `${file} 不是合法 JSON` }
  }
  let host = String(data.host ?? '')
  if (!host || host === '::' || host === '0.0.0.0') host = '127.0.0.1'
  const port = Number(data.port)
  if (!Number.isFinite(port) || port <= 0) {
    return { ok: false, error: 'webui.json 里没有有效端口' }
  }
  return {
    ok: true,
    host,
    port,
    token: String(data.token ?? ''),
    disabled: Boolean(data.disableWebUI),
  }
}

/** 从 fetch 抛出的错误里挖出真正的错误码（undici 把它埋在 cause 里）。 */
function errCode(err) {
  const seen = new Set()
  const walk = (e, depth) => {
    if (!e || depth > 4 || seen.has(e)) return ''
    if (typeof e === 'object') seen.add(e)
    if (typeof e?.code === 'string' && e.code) return e.code
    for (const next of [e?.cause, ...(Array.isArray(e?.errors) ? e.errors : [])]) {
      const hit = walk(next, depth + 1)
      if (hit) return hit
    }
    return ''
  }
  return walk(err, 0)
}

/** 判断是不是「NapCat 那台 WebUI 根本没开着」。 */
function isDown(code, err) {
  if (err?.name === 'TimeoutError') return true
  if (code === 'ECONNREFUSED' || code === 'ECONNRESET' || code === 'EHOSTUNREACH') return true
  if (code === 'ENOTFOUND' || code === 'EAI_AGAIN' || code === 'ETIMEDOUT') return true
  return String(err?.message ?? '') === 'fetch failed'
}

/** POST 一个 NapCat WebUI 接口，返回解析后的 JSON。所有网络故障都翻成人话。 */
async function webuiFetch(webui, api, payload, timeoutMs) {
  const url = `http://${webui.host}:${webui.port}/api/${api}`
  const headers = { 'content-type': 'application/json' }
  if (webui.credential) headers.authorization = `Bearer ${webui.credential}`
  let res
  try {
    res = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload ?? {}),
      signal: AbortSignal.timeout(timeoutMs),
    })
  } catch (err) {
    const code = errCode(err)
    if (isDown(code, err)) {
      throw new Error(
        `NapCat 没在运行：${webui.host}:${webui.port} 连不上（${code || '连接失败'}）。` +
          '请先启动 NapCat（开了 QQ 和 NapCat 的启动器）再重试。',
      )
    }
    throw new Error(`请求 NapCat ${api} 失败：${err?.message || err}`)
  }
  const text = await res.text()
  try {
    return JSON.parse(text)
  } catch {
    throw new Error(`${api} 返回了非 JSON（HTTP ${res.status}）：${text.slice(0, 120)}`)
  }
}

/**
 * Credential 缓存。
 *
 * 为什么必须有：NapCat 的 `auth/login` 有频率限制，短时间内登录次数一多就直接回
 * `{"code":-1,"message":"login rate limit"}`，而且会持续一段时间不恢复（实测连打
 * 8 次全部 rate limit）。切换开关一次要调 4~6 个 WebUI 接口，每次都重新登录的话
 * 必然撞上它——用户看到的就是「切换失败：WebUI 登录失败：login rate limit」。
 *
 * 缓存键用 configDir + token：换了账号或改了 token 就自动失效。
 */
const credentialCache = new Map()

/** 清掉缓存。切号 / 重启 NapCat 之后旧 Credential 会失效，需要主动清。 */
export function clearCredentialCache() {
  credentialCache.clear()
}

/**
 * NapCat 判定「凭据无效」的统一形态：HTTP 200 + code -1 + message Unauthorized。
 *
 * 为什么必须单独认它：Credential 缓存在插件进程的内存里，而 NapCat 随时可能被
 * 重启（用户手动重启、切号、崩溃重启）。重启后旧 Credential 立即作废，但插件
 * 还拿着它去调接口，于是每个请求都回 Unauthorized——用户看到的就是
 * 「设置自动登录号失败：Unauthorized」，而单独跑一次脚本又是好的（进程是新的）。
 */
function isUnauthorized(body) {
  if (body?.code !== -1) return false
  return /unauthori[sz]ed/i.test(String(body?.message ?? ''))
}

/**
 * 带凭据调一个 WebUI 接口；**一旦撞上 Unauthorized 就丢掉缓存、重新登录、重试一次**。
 *
 * 只重试一次：Unlimited 重试会在 token 真的写错时变成死循环。
 * @returns `{ ok: true, body }` 或 `{ ok: false, error }`（登录本身失败）。
 */
async function authedFetch(configDir, api, payload, timeoutMs) {
  const webui = await webuiLogin(configDir, timeoutMs)
  if (!webui.ok) return { ok: false, error: webui.error }

  let body = await webuiFetch(webui, api, payload, timeoutMs)
  if (isUnauthorized(body)) {
    credentialCache.delete(`${configDir}\u0000${webui.token}`)
    const again = await webuiLogin(configDir, timeoutMs)
    if (!again.ok) return { ok: false, error: again.error }
    body = await webuiFetch(again, api, payload, timeoutMs)
    if (isUnauthorized(body)) {
      return {
        ok: false,
        error:
          'NapCat 拒绝了刚签发的凭据（Unauthorized）。通常是 webui.json 里的 token ' +
          '与 NapCat 实际使用的不一致，或者 NapCat 刚重启还没就绪。等几秒再试一次。',
      }
    }
  }
  return { ok: true, body }
}

/** 登录 WebUI 拿 Credential。token 的算法固定是 SHA256(token + ".napcat")。 */
export async function webuiLogin(configDir, timeoutMs = 6000) {
  const cfg = readWebuiConfig(configDir)
  if (!cfg.ok) return cfg

  const cacheKey = `${configDir}\u0000${cfg.token}`
  const cached = credentialCache.get(cacheKey)
  if (cached) return { ...cfg, ok: true, credential: cached }

  const hash = crypto.createHash('sha256').update(`${cfg.token}.napcat`, 'utf8').digest('hex')

  // 撞上限流就退避重试几次：NapCat 的限流窗口通常只有几秒。
  const delays = [0, 1200, 2500, 5000]
  let lastMessage = ''
  for (const delay of delays) {
    if (delay) await new Promise((r) => setTimeout(r, delay))
    const body = await webuiFetch({ ...cfg, credential: '' }, 'auth/login', { hash, totpCode: '' }, timeoutMs)
    const credential = body?.data?.Credential
    if (credential) {
      credentialCache.set(cacheKey, credential)
      return { ...cfg, ok: true, credential }
    }
    lastMessage = String(body?.message || '')
    // 只有限流值得重试；token 错 / 别的错重试也没用。
    if (!/rate limit/i.test(lastMessage)) break
  }

  const why = /rate limit/i.test(lastMessage)
    ? 'NapCat 的 WebUI 登录被限流了（短时间内登录太多次）。等 10~30 秒再点一次就行。'
    : `WebUI 登录失败：${lastMessage || '没有返回 Credential'}（检查 NapCat 的 webui.json token 有没有改过）`
  return { ok: false, error: why }
}

/** 取当前登录的 QQ 号与昵称。 */
export async function getLoginInfo(configDir, timeoutMs = 6000) {
  const r = await authedFetch(configDir, 'QQLogin/GetQQLoginInfo', {}, timeoutMs)
  if (!r.ok) return r
  const data = r.body?.data
  if (!data || !data.uin) {
    return { ok: false, error: `NapCat 没有返回登录信息：${r.body?.message || '未知原因'}` }
  }
  return {
    ok: true,
    uin: String(data.uin),
    nick: String(data.nick ?? ''),
    online: data.online !== false,
    coreReady: true,
  }
}

/** 设置下次自动登录的 QQ 号。 */
export async function setQuickLoginQQ(configDir, uin, timeoutMs = 8000) {
  const r = await authedFetch(configDir, 'QQLogin/SetQuickLoginQQ', { uin: String(uin) }, timeoutMs)
  if (!r.ok) return r
  const body = r.body
  if (body?.code !== 0) {
    return { ok: false, error: `设置自动登录号失败：${body?.message || `code=${body?.code}`}` }
  }
  return { ok: true, uin: String(uin) }
}

/**
 * 读 NapCat 当前内存里的 OneBot 配置（含 network.websocketClients）。
 *
 * 注意 SetConfig/GetConfig 操作的是**当前登录号**的配置，不是任意 QQ 号，
 * 所以调用前必须先确认登录的是哪个号。
 */
export async function getOb11Config(configDir, timeoutMs = 8000) {
  const r = await authedFetch(configDir, 'OB11Config/GetConfig', {}, timeoutMs)
  if (!r.ok) return r
  const body = r.body
  if (body?.code !== 0 || !body?.data) {
    return { ok: false, error: `读 OneBot 配置失败：${body?.message || `code=${body?.code}`}` }
  }
  return { ok: true, config: body.data }
}

/**
 * 写回 OneBot 配置。NapCat 收到后会**立即热重载网络配置**（不用重启 QQ 进程），
 * 同时把它落盘到当前登录号的 onebot11_<QQ号>.json。
 *
 * 这一点很重要：热重载意味着切号时如果账号已经对了，就完全不必重启 QQ，
 * 也就不会打断用户的聊天会话。
 */
export async function setOb11Config(configDir, config, timeoutMs = 10000) {
  const r = await authedFetch(configDir, 'OB11Config/SetConfig', { config: JSON.stringify(config) }, timeoutMs)
  if (!r.ok) return r
  const body = r.body
  if (body?.code !== 0) {
    return { ok: false, error: `写 OneBot 配置失败：${body?.message || `code=${body?.code}`}` }
  }
  return { ok: true }
}

/**
 * 把「当前登录号」的反向 WS 客户端设成指定列表，走 SetConfig 热重载。
 *
 * 和 setOnlyClients 的区别：这个不动文件、立即生效、不需要重启；
 * setOnlyClients 是给「还没登上来、下次登录才读文件」的场景用的。
 */
export async function hotSetClients(configDir, clients, timeoutMs = 10000) {
  const cur = await getOb11Config(configDir, timeoutMs)
  if (!cur.ok) return cur
  const cfg = cur.config && typeof cur.config === 'object' ? { ...cur.config } : {}
  if (!cfg.network || typeof cfg.network !== 'object') cfg.network = {}
  const before = Array.isArray(cfg.network.websocketClients) ? cfg.network.websocketClients : []
  cfg.network.websocketClients = clients
  const written = await setOb11Config(configDir, cfg, timeoutMs)
  if (!written.ok) return written
  return { ok: true, before, after: clients }
}

/** 让 NapCat 重启 QQ 进程并按自动登录号重新登录。 */
export async function restartNapCat(configDir, timeoutMs = 10000) {
  const r = await authedFetch(configDir, 'QQLogin/RestartNapCat', {}, timeoutMs)
  // 重启后旧 Credential 立即作废（哪怕这次请求本身失败了，NapCat 也可能已经
  // 在重启路上），缓存必须清掉，否则后面每个接口都 Unauthorized。
  clearCredentialCache()
  if (!r.ok) return r
  const body = r.body
  if (body?.code !== 0) {
    return { ok: false, error: `重启 NapCat 失败：${body?.message || `code=${body?.code}`}` }
  }
  return { ok: true }
}

/** 等到指定 QQ 号登录成功，或超时。 */
export async function waitForLogin(configDir, uin, timeoutMs = 45000) {
  const deadline = Date.now() + timeoutMs
  let lastError = ''
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 1500))
    try {
      const info = await getLoginInfo(configDir, 5000)
      if (info.ok && info.uin === String(uin) && info.online) {
        return { ok: true, ...info }
      }
      lastError = info.ok ? `当前登录的是 ${info.uin}` : info.error
    } catch (err) {
      lastError = err?.message || String(err)
    }
  }
  return { ok: false, error: `等待 ${uin} 登录超时：${lastError}` }
}

// ---------- onebot11_<QQ号>.json 读写 ----------

/** 读某个 QQ 号的反向 WS 配置文件。 */
export function readOnebotFile(configDir, uin) {
  const file = path.join(configDir, `onebot11_${uin}.json`)
  let raw
  try {
    raw = fs.readFileSync(file, 'utf8')
  } catch {
    return { exists: false, file, raw: null, data: null }
  }
  let data
  try {
    data = JSON.parse(raw)
  } catch {
    return { exists: true, file, raw, data: null, error: '不是合法 JSON' }
  }
  return { exists: true, file, raw, data }
}

/**
 * 把这个 QQ 号的反向 WS 客户端列表设成「只连我们要的那一条」。
 *
 * 关键点：不是「追加一条」，而是「替成恰好这一条」。
 * 因为「开启时不发言」靠的就是把这个号通往 AstrBot(6199) 的客户端条目物理删掉——
 * AstrBot 是反向 WS 的服务端，NapCat 不主动连它，它就连不上这个号，也就发不出话。
 */
export function setOnlyClients(configDir, uin, clients) {
  const current = readOnebotFile(configDir, uin)
  const base = current.data && typeof current.data === 'object' ? { ...current.data } : {}
  if (!base.network || typeof base.network !== 'object') base.network = {}
  base.network.websocketClients = clients

  const text = `${JSON.stringify(base, null, 2)}\n`
  const file = path.join(configDir, `onebot11_${uin}.json`)
  const tmp = `${file}.qqark-tmp`
  fs.writeFileSync(tmp, text, 'utf8')
  fs.renameSync(tmp, file)
  return { ok: true, file, before: current.raw, after: text }
}

/** 构造一条反向 WS 客户端配置。 */
export function makeClient(name, url, extra) {
  return {
    enable: true,
    name,
    url,
    reportSelfMessage: false,
    messagePostFormat: 'array',
    token: '',
    debug: false,
    heartInterval: 30000,
    reconnectInterval: 3000,
    verifyCertificate: true,
    ...extra,
  }
}

// ---------- NapCat 自身的启动器 ----------

/**
 * NapCat 安装根目录的候选位置。
 *
 * NAPCAT_ROOT 一旦设置就**只认它**（不再回退到别的候选）——
 * 否则测试/调试时想隔离一个假目录，最后会静默回退到真实目录，
 * 把真的 NapCat 拉起来（本轮就因此误启动过一次 QQ）。
 */
function napcatRoots() {
  const pinned = String(process.env.NAPCAT_ROOT ?? '').trim()
  if (pinned) return [pinned]

  const list = []
  const push = (p) => {
    if (p) list.push(p)
  }
  const home = os.homedir()
  // OneKey 版通常解压在「下载」里；解压到桌面/文档的也不少，一起扫。
  for (const base of ['Downloads', 'Desktop', 'Documents']) {
    const dir = path.join(home, base)
    try {
      for (const name of fs.readdirSync(dir)) {
        if (!/napcat/i.test(name)) continue
        push(path.join(dir, name))
        // OneKey 包里真正的 Shell 在子目录（如 NapCat.52230.Shell），名字随版本变，这里扫一层。
        try {
          for (const sub of fs.readdirSync(path.join(dir, name))) {
            if (/napcat.*shell/i.test(sub)) push(path.join(dir, name, sub))
          }
        } catch {
          /* 子目录不可读就跳过 */
        }
      }
    } catch {
      /* 没有这个目录就算了 */
    }
  }
  return list
}

/**
 * 找 NapCat 的启动脚本。
 *
 * **第一优先：OneKey 根目录的 `napcat.bat`（0.5.1 修，load-bearing）**。
 *
 * 为什么不能先用 `launcher.bat`：OneKey 一键版本**自带一份完整的 QQ 内核**
 * （`<root>\versions\<ver>\`，含 214MB 的 QQNT.dll），`napcat.bat` 不带参数调
 * 同目录的 `NapCatWinBootMain.exe`，后者取自身目录拼 `\QQ.exe`，用的就是自带的
 * 那个内核。而 `versions\<ver>\resources\app\napcat\launcher.bat` 是另一套逻辑：
 * 它去**卸载注册表**反推 QQ 安装目录再拼 `QQ.exe`，于是加载到系统装的那个 QQ。
 *
 * 代价（2026-10-31 实测）：系统 QQ 自动升到 9.9.35-52973，而 NapCat 52230 的
 * packetBackend 只支持 QQ 28418–36580（官方文档 config/advanced），于是
 * `get_group_file_url` 一律 `retcode=1400 packetBackend 不可用`，
 * 全群文件都换不到下载链接 —— 面板上就表现为"一批一批下载失败"。
 *
 * 所以**不要**为了"launcher.bat 会自己提权"就把它排前面：包内内核能正常跑，
 * 而提权与否跟读文件内容无关。
 *
 * 兜底顺序：根目录 `napcat.bat` → `launcher.bat`（会自提权，但可能挑错 QQ 版本）
 * → `launcher-user.bat`。
 */
export function findNapcatLauncher() {
  const tried = []
  const roots = napcatRoots()

  // 1) OneKey 根目录的 napcat.bat：用内置 QQ 内核，不经注册表
  for (const root of roots) {
    const bat = path.join(root, 'napcat.bat')
    tried.push(bat)
    if (!fs.existsSync(bat)) continue
    // 这三个文件在同一层才能确认是"自带内核"的布局
    const booter = path.join(root, 'NapCatWinBootMain.exe')
    const kernel = path.join(root, 'QQ.exe')
    if (!fs.existsSync(booter) || !fs.existsSync(kernel)) continue
    return { ok: true, file: bat, dir: root, root, elevated: false, kernel: 'bundled' }
  }

  // 2) 兜底：resources\app\napcat 下的 launcher*.bat（读注册表挑系统 QQ）
  for (const root of roots) {
    const versionsDir = path.join(root, 'versions')
    let versions = []
    try {
      versions = fs.readdirSync(versionsDir)
    } catch {
      continue
    }
    for (const ver of versions) {
      const dir = path.join(versionsDir, ver, 'resources', 'app', 'napcat')
      for (const name of ['launcher.bat', 'launcher-user.bat']) {
        const file = path.join(dir, name)
        tried.push(file)
        if (fs.existsSync(file)) {
          return { ok: true, file, dir, root, elevated: name === 'launcher.bat', kernel: 'system' }
        }
      }
    }
  }
  return {
    ok: false,
    error: '找不到 NapCat 的启动脚本（napcat.bat / launcher.bat / launcher-user.bat）',
    tried,
  }
}

/** 读 <root>\versions\config.json 里的 curVersion（拿不到就返回空串）。 */
function bundledVersion(root) {
  try {
    const raw = fs.readFileSync(path.join(root, 'versions', 'config.json'), 'utf8')
    const cfg = JSON.parse(raw)
    return String(cfg.curVersion || cfg.baseVersion || '').trim()
  } catch {
    return ''
  }
}

/** 包内自带内核的 QQNT.dll 应该在哪儿。返回候选绝对路径（小写去重，便于比较）。 */
function bundledKernelDlls() {
  const seen = new Set()
  for (const root of napcatRoots()) {
    const ver = bundledVersion(root)
    if (!ver) continue
    const candidates = [
      path.join(root, 'versions', ver, 'QQNT.dll'),
      path.join(root, 'Files', 'versions', ver, 'QQNT.dll'),
    ]
    for (const c of candidates) {
      if (!fs.existsSync(c)) continue
      seen.add(path.resolve(c).toLowerCase())
    }
  }
  return [...seen]
}

/** 从 QQNT.dll 路径里抠出 9.9.xx-yyyyy 这样的版本号。 */
function versionFromDll(dllPath) {
  const m = /[/\\](\d+\.\d+\.\d+-\d+)[/\\]/i.exec(String(dllPath || ''))
  return m ? m[1] : ''
}

/**
 * 探测「当前真正在跑的 QQ 用的是哪个内核」（0.5.2 加，load-bearing）。
 *
 * 为什么需要它：packetBackend 只认特定范围的 QQ 版本。如果 NapCat 已经在跑、
 * 但加载的是系统装的那个新版 QQ，那么 `ensureNapCatRunning` 会直接说「已在运行」
 * 然后返回 —— 我们改用 napcat.bat 的修复根本不会生效，用户看到的报错照旧。
 *
 * 怎么探：QQ 进程是可执行文件 + `QQNT.dll` 的组合，dll 的路径直接暴露了内核
 * 版本（`...\versions\9.9.35-52973\QQNT.dll`）。用 PowerShell 的
 * `(Get-Process QQ).Modules` 读它。提权进程读不到模块，那就读不到 —— 这种
 * 情况返回 `ok:false`，调用方**只警告不动手**，绝不猜。
 *
 * **多个 QQ 进程时以监听 napcatPort 的那个为准（0.5.2 实测修正）**。
 * 实测踩过的坑：旧实例（系统 9.9.35）还占着 6099，同时新实例（包内 9.9.33）
 * 已经在跑并顺延到 6100。如果只取「第一个能读出内核的进程」，就会报 9.9.33
 * 一切正常，而实际上 NapCat 用的还是那个 9.9.35 —— 判断完全反了。
 *
 * @param {number} [napcatPort] NapCat WebUI 端口；给了就优先用它来确定「哪个进程是当前这个」。
 */
export function detectRunningKernel(napcatPort) {
  if (process.platform !== 'win32') {
    return { ok: false, reason: '只有 Windows 需要判断 QQ 内核版本' }
  }
  const ps = [
    'Get-Process QQ -ErrorAction SilentlyContinue | ForEach-Object {',
    '  try { $d = $_.Modules | Where-Object { $_.ModuleName -eq "QQNT.dll" } | Select-Object -First 1 } catch { $d = $null }',
    '  if ($d) { "{0}|{1}" -f $_.Id, $d.FileName }',
    '}',
  ].join('\n')

  let out = ''
  try {
    out = execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', ps], {
      encoding: 'utf8',
      timeout: 20000,
      windowsHide: true,
    })
  } catch (err) {
    return { ok: false, reason: `读不到 QQ 进程的内核信息：${err?.message || err}` }
  }

  const rows = String(out)
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.includes('|'))
    .map((line) => {
      const at = line.indexOf('|')
      return { pid: Number(line.slice(0, at)), dll: line.slice(at + 1).trim() }
    })
    .filter((row) => row.dll)

  if (!rows.length) {
    return { ok: false, reason: '没有正在运行的 QQ 进程，或所有 QQ 进程都读不到模块（提权运行）' }
  }

  const bundled = bundledKernelDlls()
  let running = rows.map((row) => {
    const dll = path.resolve(row.dll)
    return {
      pid: row.pid,
      dll,
      version: versionFromDll(dll),
      bundled: bundled.includes(dll.toLowerCase()),
    }
  })

  // 给了端口就看看是谁在听，把那个进程排到最前面
  let portPid = 0
  if (napcatPort) {
    try {
      const psPort = `(Get-NetTCPConnection -LocalPort ${Number(napcatPort)} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1).OwningProcess`
      const raw = execFileSync(
        'powershell.exe',
        ['-NoProfile', '-NonInteractive', '-Command', psPort],
        { encoding: 'utf8', timeout: 20000, windowsHide: true },
      )
      portPid = Number(String(raw).trim()) || 0
    } catch {
      /* 拿不到就退回顺序判断 */
    }
  }
  if (portPid) {
    const hit = running.filter((r) => r.pid === portPid)
    if (hit.length) running = [...hit, ...running.filter((r) => r.pid !== portPid)]
  }

  const first = running[0]
  return {
    ok: true,
    version: first.version,
    // true = 用的包内自带内核；false = 用的外面装的 QQ
    bundled: first.bundled,
    pid: first.pid,
    portPid,
    // 同一个版本号出现在别的进程上，说明有并存的实例（诊断用过）
    mixed: running.some((r) => r.bundled !== first.bundled),
    pids: running.map((r) => r.pid),
    running,
    bundledDlls: bundled,
  }
}

/**
 * 杀掉所有 QQ 进程（以及 NapCat 的引导进程），好让下一个启动器重新决定加载哪个内核。
 *
 * 只有在探测到「正在跑系统那一版 QQ、packetBackend 因此报废」时才调用，
 * 而且调用方必须先告诉用户。注意：这会中断当前登录，NapCat 需要重新拉起。
 *
 * **为什么要 UAC（0.5.2 实测，load-bearing）**：旧 NapCat 常常是提权运行的，
 * 普通权限的 `taskkill /f /im QQ.exe` 会只杀掉自己能碰到的那个进程，其余直接
 * 静默失败（实测 4 个进程里只杀掉 1 个，而 `taskkill` 的退出码仍是 0），于是
 * 旧实例继续占着 6099，新实例只好顺延到 6100 —— 两个 NapCat 并存，看起来
 * 「换好了」其实一点都没换。所以先试普通权限，确认还有残留后再走一次 UAC。
 *
 * @param {{elevate?: boolean}} [opts] elevate 显式要求直接走 UAC。
 */
export function killQQ(opts = {}) {
  const run = (command, args) => {
    try {
      const stdout = execFileSync(command, args, {
        encoding: 'utf8',
        timeout: 30000,
        windowsHide: true,
      })
      return { ok: true, out: String(stdout || '') }
    } catch (err) {
      // taskkill 在「没有这个进程」时也会以非 0 退出，不当成失败
      const msg = String(err?.stderr || err?.stdout || err?.message || err).trim()
      if (/not found|没有找到|找不到|not running/i.test(msg)) return { ok: true, out: msg }
      return { ok: false, error: msg || 'taskkill 失败' }
    }
  }

  const stillAlive = () => {
    const ps = "(Get-Process QQ -ErrorAction SilentlyContinue | Measure-Object).Count"
    try {
      const raw = execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', ps], {
        encoding: 'utf8',
        timeout: 20000,
        windowsHide: true,
      })
      return Number(String(raw).trim()) || 0
    } catch {
      return -1
    }
  }

  const first = run('taskkill.exe', ['/f', '/im', 'QQ.exe', '/im', 'NapCatWinBootMain.exe'])
  const leftAfterPlain = stillAlive()
  if (!opts.elevate && leftAfterPlain <= 0) {
    return { ok: true, elevated: false }
  }

  /**
   * 走 UAC。用 `Start-Process -Verb RunAs -Wait` 并取 ExitCode，这样可以确认
   * 用户到底有没有点「是」。注意 UAC 框可能弹在安全桌面上（PromptOnSecureDesktop），
   * 用户看不到就等于超时 —— 所以这里必须把「要点框」这件事报回去。
   */
  const inner = 'taskkill /f /im QQ.exe /im NapCatWinBootMain.exe'
  const uac = [
    "$p = Start-Process -FilePath 'taskkill.exe' -ArgumentList '/f','/im','QQ.exe','/im','NapCatWinBootMain.exe' -Verb RunAs -PassThru -Wait -ErrorAction Stop",
    'exit $p.ExitCode',
  ].join('\n')
  let elevated = false
  let uacError = ''
  try {
    execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', uac], {
      encoding: 'utf8',
      timeout: 120000,
      windowsHide: true,
    })
    elevated = true
  } catch (err) {
    uacError = String(err?.stderr || err?.message || err).trim()
  }

  // 给进程一点时间真的退干净
  let left = stillAlive()
  for (let i = 0; i < 10 && left !== 0; i += 1) {
    // 同步等待（这里在工具调用里跑，用 Atomics 睡一下不被 GC 打断）
    const until = Date.now() + 400
    while (Date.now() < until) {
      /* busy-ish wait，极短 */
    }
    left = stillAlive()
  }

  if (left === 0) {
    return {
      ok: true,
      elevated,
      note: elevated
        ? '旧的内核是提权运行的，普通权限杀不掉，已通过管理员权限结束'
        : '',
    }
  }

  return {
    ok: false,
    elevated,
    error:
      `${left} 个 QQ 进程还活着（旧 NapCat 是以管理员权限启动的，普通权限杀不掉）。` +
      (uacError
        ? `\n管理员授权没有通过：${uacError}`
        : '\n管理员授权框可能弹在了安全桌面上，没被看到。') +
      '\n请手动操作：右下角任务栏右键 QQ → 退出；或在任务管理器里结束所有 QQ.exe。' +
      '\n然后重新点「开启」——那时会用包内自带的内核启动。',
    firstError: first.ok ? '' : first.error,
    inner,
  }
}

/**
 * 把 NapCat 拉起来（不占住插件的进程）。
 *
 * 三个坑：
 * 1. 目录里含空格（`NapCat.Shell.Windows.OneKey`），所以不能把整条路径塞给
 *    `cmd /c`——cmd 会把空格当参数分隔符拆开。这里改成「cwd 带目录 + 只传文件名」，
 *    cwd 是 spawn 的选项，不经过 cmd 的词法解析。
 * 2. bat 末尾带 pause，一旦把 stdin 接过来子进程会一直挂着等回车，
 *    所以 stdio 必须全 ignore。
 * 3. **不能加 `detached: true`**（0.4.3 修）。实测在 Windows 上它会连带
 *    `DETACHED_PROCESS` 一起传下去，没有控制台的子进程启动即退，spawn 却不报错，
 *    于是「看起来发了启动命令、实际什么都没发生」。要去掉父进程的束缚，
 *    靠 `cmd /c` 自身的进程交接就够了。
 */
export function launchNapCat(file) {
  const dir = path.dirname(file)
  try {
    const child = spawn('cmd.exe', ['/c', path.basename(file)], {
      cwd: dir,
      stdio: 'ignore',
      windowsHide: true,
    })
    child.unref()
    return { ok: true, via: file, pid: child.pid }
  } catch (err) {
    return { ok: false, error: `启动 NapCat 失败：${err?.message || err}` }
  }
}

/** 轮询等某个端口起来。 */
export async function waitPort(port, host = '127.0.0.1', timeoutMs = 60000, intervalMs = 1200) {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    if (await portListening(port, host, 900)) return true
    if (Date.now() >= deadline) return false
    await new Promise((r) => setTimeout(r, intervalMs))
  }
}

/** 探测端口上有没有在监听（用 Node 自带的 net，不引三方包）。 */
export function portListening(port, host = '127.0.0.1', timeoutMs = 800) {
  return new Promise((resolve) => {
    let settled = false
    const done = (value) => {
      if (settled) return
      settled = true
      resolve(value)
    }
    // 延迟 require，避免模块顶层引入 net 影响宿主启动路径
    import('node:net').then(({ default: net }) => {
      const socket = new net.Socket()
      socket.setTimeout(timeoutMs)
      socket.once('connect', () => {
        socket.destroy()
        done(true)
      })
      socket.once('timeout', () => {
        socket.destroy()
        done(false)
      })
      socket.once('error', () => {
        socket.destroy()
        done(false)
      })
      try {
        socket.connect(port, host)
      } catch {
        done(false)
      }
    }, () => done(false))
    setTimeout(() => done(false), timeoutMs + 500)
  })
}

// ---------- 切换状态持久化 ----------

/**
 * 状态文件放在归档器程序目录的 data 下：插件自身是只读安装副本，
 * 重装会被覆盖；放在归档器旁边能跟着用户的数据一起活着。
 */
export function stateFile(appDir) {
  return path.join(appDir, 'data', 'qqark-switch.json')
}

export function readState(appDir) {
  const file = stateFile(appDir)
  try {
    const data = JSON.parse(fs.readFileSync(file, 'utf8'))
    return data && typeof data === 'object' ? data : {}
  } catch {
    return {}
  }
}

export function writeState(appDir, state) {
  const file = stateFile(appDir)
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true })
  } catch {
    /* 目录已存在 */
  }
  fs.writeFileSync(file, `${JSON.stringify(state, null, 2)}\n`, 'utf8')
  return { ok: true, file }
}

/**
 * 切换日志（追加写）。为什么单独留一份：
 *
 * 归档器的 data/qqark.log 只记归档器自己的事；切号、提权、断外挂这些动作全发生在
 * 插件进程里，出问题时用户问「上次开启到底做到哪一步」，日志里一个字都没有。
 * 这里把插件的动作也落成文本，UI 的「日志」页就能把两边合起来按时间看。
 */
export function switchLogFile(appDir) {
  return path.join(appDir, 'data', 'qqark-plugin.log')
}

export function appendSwitchLog(appDir, entry) {
  if (!appDir) return { ok: false, error: '没有程序目录' }
  const file = switchLogFile(appDir)
  const at = entry?.at || new Date().toISOString()
  const kind = String(entry?.kind || 'switch')
  const level = entry?.ok === false ? 'ERROR' : 'INFO '
  const line = `${at} ${level} [${kind}] ${String(entry?.detail ?? '')}\n`
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true })
  } catch {
    /* 目录已存在 */
  }
  try {
    fs.appendFileSync(file, line, 'utf8')
    // 防止无限变大：超过 512KB 就只留尾部 2000 行
    if (fs.statSync(file).size > 512 * 1024) {
      const kept = fs.readFileSync(file, 'utf8').split('\n').slice(-2000).join('\n')
      fs.writeFileSync(file, kept, 'utf8')
    }
    return { ok: true, file }
  } catch (err) {
    return { ok: false, error: String(err?.message || err) }
  }
}

/** 把一份原始文本还原回 onebot11_<QQ号>.json。 */
export function restoreOnebotFile(configDir, uin, raw) {
  const file = path.join(configDir, `onebot11_${uin}.json`)
  if (raw === null || raw === undefined) {
    try {
      fs.rmSync(file, { force: true })
    } catch {
      /* 本来就没有 */
    }
    return { ok: true, file, removed: true }
  }
  const tmp = `${file}.qqark-tmp`
  fs.writeFileSync(tmp, raw, 'utf8')
  fs.renameSync(tmp, file)
  return { ok: true, file, removed: false }
}
