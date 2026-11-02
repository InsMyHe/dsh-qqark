#!/usr/bin/env node
/**
 * 插件包契约校验。
 *
 * 这些断言和 dsh-plugin-dev check 的重点重叠，但不需要那份工具链，
 * 因此在 CI 里也能跑：manifest 字段、bundle patch、随包交付的文件、
 * 五语 README 是否齐全且二级标题一致。
 *
 * 用法：node scripts/check-plugin-contract.mjs [插件目录]
 */

import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

const root = process.argv[2] ?? 'dsh-plugin-qqark'
const errors = []

function fail(msg) {
  errors.push(msg)
}

function readText(rel) {
  const p = join(root, rel)
  return existsSync(p) ? readFileSync(p, 'utf8') : ''
}

/** 只认二级标题 `## `，与 dsh-plugin-guide 的 readme-consistency 口径一致。 */
function headingsOf(text) {
  return text
    .split(/\r?\n/)
    .filter((l) => l.startsWith('## '))
    .map((l) => l.slice(3).trim())
}

const pkgPath = join(root, 'package.json')
if (!existsSync(pkgPath)) {
  console.error(`✗ 找不到 ${pkgPath}`)
  process.exit(1)
}

const pkg = JSON.parse(readFileSync(pkgPath, 'utf8'))

// ---- manifest ----
if (pkg.name !== 'dsh-plugin-qqark') fail(`package name 应为 dsh-plugin-qqark，实际 ${pkg.name}`)
if (pkg.type !== 'module') fail('package 必须是 ESM（type: "module"）')
if (!pkg.main) fail('缺少 main 入口')
if (pkg.main && !existsSync(join(root, pkg.main))) fail(`main 指向的文件不存在：${pkg.main}`)

if (!pkg.dsh) fail('缺少 dsh 清单段')
else {
  if (!pkg.dsh.bundle || pkg.dsh.bundle.patch !== './cordis.patch.yml') {
    fail('dsh.bundle.patch 必须指向 ./cordis.patch.yml')
  }
  if (!pkg.dsh.client || pkg.dsh.client.platform !== 'web') {
    fail('dsh.client.platform 必须是 web')
  }
  if (pkg.dsh.manifestVersion !== 1) fail('dsh.manifestVersion 必须是 1')
}

if (!pkg.packageManager) fail('packageManager 未固定（请写 pnpm@11.7.0）')
if (!pkg.engines || !pkg.engines.node) fail('缺少 engines.node')

// ---- 随包交付的文件 ----
const shipped = [
  'index.js',
  'client.js',
  'napcat.js',
  'cordis.patch.yml',
  'icon.svg',
  'locale/zh.json',
  'locale/en.json',
]
for (const f of shipped) {
  if (!existsSync(join(root, f))) fail(`缺少随包文件：${f}`)
}
if (Array.isArray(pkg.files)) {
  for (const f of ['index.js', 'client.js', 'napcat.js', 'cordis.patch.yml']) {
    if (!pkg.files.includes(f)) fail(`files 白名单漏了 ${f}`)
  }
} else {
  fail('package.json 缺少 files 白名单')
}

// ---- 五语 README ----
const langs = ['README.md', 'README-zh.md', 'README-es.md', 'README-pt.md', 'README-hi.md']
for (const l of langs) {
  if (readText(l).trim() === '') fail(`README 缺失或为空：${l}`)
}

const base = headingsOf(readText('README.md'))
if (base.length === 0) fail('README.md 没有任何二级标题')
for (const l of langs.slice(1)) {
  const text = readText(l)
  if (!text.trim()) continue
  const have = headingsOf(text)
  const missing = base.filter((h) => !have.includes(h))
  if (missing.length > 0) fail(`${l} 缺少二级标题：${missing.join(', ')}`)
}

// ---- cordis.patch.yml ----
const patch = readText('cordis.patch.yml')
if (!patch.trim()) fail('cordis.patch.yml 为空')
else if (!/- id:\s*dsh-plugin-qqark/.test(patch)) fail('cordis.patch.yml 里没有 id: dsh-plugin-qqark 这一行')

// ---- 报告 ----
if (errors.length > 0) {
  console.error(`✗ 插件包契约校验失败（${errors.length} 项）：`)
  for (const e of errors) console.error(`  · ${e}`)
  process.exit(1)
}
console.log(`✓ 插件包契约校验通过（${root}）`)
