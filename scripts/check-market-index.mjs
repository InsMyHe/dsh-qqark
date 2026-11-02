// 校验 market/index.json 是否符合 DSH 插件市场索引 format 1。
// 规则逐条对齐 @deepseek-ai/dsh-plugin-market-index 的 parseMarketIndex（无依赖，纯 Node）。
//
// 用法：node scripts/check-market-index.mjs [index.json 路径] [索引 URL]
//   索引 URL 缺省取 jsDelivr 上的 @main 地址，用于校验相对图标能否解析。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const LANGUAGE_ID = /^[a-z]{2,8}(?:-[a-z0-9]{1,8})*$/u;
const PACKAGE_NAME = /^(?:@[a-z0-9][a-z0-9._~-]*\/)?[a-z0-9][a-z0-9._~-]*$/u;
const PACKAGE_NAME_MAX_LENGTH = 214;
const NUMERIC = '(?:0|[1-9]\\d*)';
const PRERELEASE_PART = `(?:${NUMERIC}|\\d*[a-zA-Z-][0-9a-zA-Z-]*)`;
const SEMVER = new RegExp(
  `^${NUMERIC}\\.${NUMERIC}\\.${NUMERIC}(?:-${PRERELEASE_PART}(?:\\.${PRERELEASE_PART})*)?(?:\\+[0-9a-zA-Z-]+(?:\\.[0-9a-zA-Z-]+)*)?$`,
  'u',
);
const COMMIT = /^[0-9a-f]{40}$/u;
const PATH_SEGMENT = /^[A-Za-z0-9._@+-]+$/u;
const DATA_ICON = /^data:(?:image\/svg\+xml|image\/png|image\/jpeg|image\/webp);base64,([A-Za-z0-9+/]*={0,2})$/u;
const BASE64 = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u;
const MAX_INLINE_ICON_BYTES = 32 * 1024;
const MAX_PUBLISH_TIMESTAMP = 1e11;

const problems = [];
function fail(path, reason) {
  problems.push(`${path} ${reason}`);
  throw new Error(`stop:${path}`);
}
function objectAt(value, path) {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) fail(path, 'must be an object');
  return value;
}
function stringAt(value, path) {
  if (typeof value !== 'string' || value.trim() === '')
    fail(path, 'must be a string with a non-whitespace character');
  return value;
}
function optional(object, key, path, read) {
  return Object.hasOwn(object, key) ? read(object[key], `${path}.${key}`) : undefined;
}
function localizedText(value, path) {
  if (typeof value === 'string') return stringAt(value, path);
  const object = objectAt(value, path);
  const text = {};
  for (const [language, member] of Object.entries(object)) {
    if (!LANGUAGE_ID.test(language)) fail(`${path}.${language}`, 'must be keyed by a lowercase language id');
    text[language] = stringAt(member, `${path}.${language}`);
  }
  if (!Object.hasOwn(text, 'en')) fail(path, 'must contain en');
  return text;
}
function declarations(value, path, read) {
  const result = {};
  for (const [id, member] of Object.entries(objectAt(value, path))) {
    if (id === '') fail(path, 'must not declare an empty id');
    result[id] = read(objectAt(member, `${path}.${id}`), `${path}.${id}`);
  }
  return result;
}
function declaredId(value, path, declared) {
  const id = stringAt(value, path);
  if (!Object.hasOwn(declared, id)) fail(path, `references undeclared id ${JSON.stringify(id)}`);
  return id;
}
function httpUrl(value, path, protocols) {
  const text = stringAt(value, path);
  let url;
  try {
    url = new URL(text);
  } catch {
    fail(path, 'must be an absolute URL');
  }
  if (!protocols.includes(url.protocol)) fail(path, `must use ${protocols.join(' or ')}`);
  if (url.username !== '' || url.password !== '') fail(path, 'must not contain credentials');
  if (url.href.includes('?') || url.href.includes('#')) fail(path, 'must not contain a query or fragment');
  return url;
}
function gitUrl(value, path) {
  const url = httpUrl(value, path, ['https:']);
  if (url.href !== value) fail(path, 'must be a canonical URL');
  if (!/^(?:\/[^/]+){2,}$/u.test(url.pathname)) fail(path, 'must name a repository with at least two path segments');
  if (/\.(?:tgz|tar\.gz)$/iu.test(url.pathname)) fail(path, 'must name a repository, not a tarball');
  return url.href;
}
function packagePath(value, path) {
  const text = stringAt(value, path);
  if (text.split('/').some((segment) => segment === '.' || segment === '..' || !PATH_SEGMENT.test(segment)))
    fail(path, 'must be a relative directory of letters, digits, and ._@+- segments without . or ..');
  return text;
}
function sourceAt(value, path) {
  const object = objectAt(value, path);
  if (object.type === 'npm') {
    optional(object, 'registry', path, (member, at) => httpUrl(member, at, ['https:', 'http:']));
    return { type: 'npm' };
  }
  if (object.type === 'git') {
    const commit = stringAt(object.commit, `${path}.commit`);
    if (!COMMIT.test(commit)) fail(`${path}.commit`, 'must be a lowercase 40-hex-digit commit SHA');
    gitUrl(object.url, `${path}.url`);
    optional(object, 'path', path, packagePath);
    return { type: 'git' };
  }
  return fail(`${path}.type`, 'must be "npm" or "git"');
}
function enginesAt(value, path) {
  optional(objectAt(value, path), 'dsh', path, (member, at) => {
    const range = stringAt(member, at);
    if (range.startsWith('workspace:')) fail(at, 'must not be a workspace: range');
    // 用 npm 自带的 semver 语义不做完整校验，这里只做形状检查
    if (!/\d/.test(range)) fail(at, 'must be a SemVer range');
    return range;
  });
}
function iconAt(value, path, indexUrl) {
  const text = stringAt(value, path);
  if (text.startsWith('data:')) {
    const payload = DATA_ICON.exec(text)?.[1];
    if (payload === undefined || payload === '' || !BASE64.test(payload))
      fail(path, 'must be a base64 SVG, PNG, JPEG, or WebP data URL');
    const decoded = (payload.length / 4) * 3 - (payload.endsWith('==') ? 2 : payload.endsWith('=') ? 1 : 0);
    if (decoded > MAX_INLINE_ICON_BYTES) fail(path, 'must not exceed 32 KiB of decoded bytes');
    return text;
  }
  let resolved;
  try {
    resolved = new URL(text, indexUrl);
  } catch {
    fail(path, 'must be a URL reference');
  }
  if (resolved.protocol !== 'https:' && (resolved.origin === 'null' || resolved.origin !== new URL(indexUrl).origin))
    fail(path, 'must resolve to https or the index origin');
  return resolved.href;
}
function versionAt(value, path) {
  const object = objectAt(value, path);
  const version = stringAt(object.version, `${path}.version`);
  if (!SEMVER.test(version)) fail(`${path}.version`, 'must be an exact SemVer version');
  const published = object.publishTimestamp;
  if (typeof published !== 'number' || !Number.isInteger(published) || published < 0 || published >= MAX_PUBLISH_TIMESTAMP)
    fail(`${path}.publishTimestamp`, 'must be an integer number of Unix seconds in [0, 10^11)');
  sourceAt(object.source, `${path}.source`);
  optional(object, 'engines', path, enginesAt);
  return version;
}

const repoRoot = fileURLToPath(new URL('..', import.meta.url));
const file = process.argv[2] ?? `${repoRoot}market/index.json`;
const indexUrl =
  process.argv[3] ?? 'https://cdn.jsdelivr.net/gh/InsMyHe/dsh-qqark@main/market/index.json';

let value;
try {
  value = JSON.parse(readFileSync(file, 'utf8'));
} catch (err) {
  console.error(`[FAIL] 读不到或解析失败：${file}\n       ${err.message}`);
  process.exit(1);
}

let title;
try {
  if (!URL.canParse(indexUrl)) fail('$', `index URL ${JSON.stringify(indexUrl)} must be an absolute URL`);
  const root = objectAt(value, '$');
  if (root.format !== 1) fail('$.format', 'must be 1');
  const metadata = root.metadata === undefined ? {} : objectAt(root.metadata, '$.metadata');
  const categories = optional(metadata, 'categories', '$.metadata', (m, at) =>
    declarations(m, at, (o, p) => localizedText(o.title, `${p}.title`)),
  ) ?? {};
  const labels = optional(metadata, 'labels', '$.metadata', (m, at) =>
    declarations(m, at, (o, p) => localizedText(o.title, `${p}.title`)),
  ) ?? {};
  const sortKeys = optional(metadata, 'sortKeys', '$.metadata', (m, at) =>
    declarations(m, at, (o, p) => {
      if (o.order !== 'asc' && o.order !== 'desc') fail(`${p}.order`, 'must be "asc" or "desc"');
      localizedText(o.title, `${p}.title`);
    }),
  ) ?? {};
  if (!Array.isArray(root.plugins)) fail('$.plugins', 'must be an array');
  const names = new Set();
  const summary = [];
  root.plugins.forEach((entry, position) => {
    const path = `$.plugins[${position}]`;
    const object = objectAt(entry, path);
    const name = stringAt(object.name, `${path}.name`);
    if (name.length > PACKAGE_NAME_MAX_LENGTH || !PACKAGE_NAME.test(name))
      fail(`${path}.name`, 'must be a valid npm package name');
    if (names.has(name)) fail(`${path}.name`, `duplicates ${name}`);
    names.add(name);
    optional(object, 'title', path, localizedText);
    optional(object, 'description', path, localizedText);
    optional(object, 'icon', path, (member, at) => iconAt(member, at, indexUrl));
    optional(object, 'author', path, stringAt);
    optional(object, 'homepage', path, (member, at) => {
      const text = stringAt(member, at);
      if (!URL.canParse(text)) fail(at, 'must be an absolute URL');
      const protocol = new URL(text).protocol;
      if (protocol !== 'https:' && protocol !== 'http:') fail(at, 'must use https: or http:');
    });
    optional(object, 'repository', path, (member, at) => {
      const repo = objectAt(member, at);
      httpUrl(repo.url, `${at}.url`, ['https:', 'http:']);
      optional(repo, 'directory', at, packagePath);
    });
    const pluginMetadata = object.metadata === undefined ? {} : objectAt(object.metadata, `${path}.metadata`);
    optional(pluginMetadata, 'category', `${path}.metadata`, (m, at) => declaredId(m, at, categories));
    optional(pluginMetadata, 'keywords', `${path}.metadata`, (m, at) => {
      if (!Array.isArray(m)) fail(at, 'must be an array');
      m.forEach((kw, i) => {
        if (typeof kw !== 'string') fail(`${at}[${i}]`, 'must be a string');
      });
    });
    optional(pluginMetadata, 'labels', `${path}.metadata`, (m, at) => {
      if (!Array.isArray(m)) fail(at, 'must be an array');
      const ids = m.map((label, i) => declaredId(label, `${at}[${i}]`, labels));
      if (new Set(ids).size !== ids.length) fail(at, 'must not repeat a label');
    });
    optional(pluginMetadata, 'sortKeys', `${path}.metadata`, (m, at) => {
      for (const [id, sortValue] of Object.entries(objectAt(m, at))) {
        declaredId(id, `${at}.${id}`, sortKeys);
        if (typeof sortValue !== 'number' || !Number.isFinite(sortValue))
          fail(`${at}.${id}`, 'must be a finite number');
      }
    });
    if (!Array.isArray(object.versions) || object.versions.length === 0)
      fail(`${path}.versions`, 'must be a non-empty array');
    const seen = new Set();
    const versions = object.versions.map((member, i) => {
      const at = `${path}.versions[${i}]`;
      const version = versionAt(member, at);
      if (seen.has(version)) fail(`${at}.version`, `duplicates ${version}`);
      seen.add(version);
      return version;
    });
    summary.push(`${name} (${versions.join(', ')})`);
  });
  title = localizedText(root.title, '$.title');
  optional(root, 'description', '$', localizedText);
  for (const line of summary) console.log(`     ✓ ${line}`);
} catch (err) {
  if (!String(err.message).startsWith('stop:')) throw err;
  console.error(`[FAIL] market 索引不符合 format 1：${file}`);
  for (const problem of problems) console.error(`       ${problem}`);
  process.exit(1);
}

console.log(`[OK] market 索引通过 format 1 校验：${typeof title === 'string' ? title : (title.en ?? JSON.stringify(title))}`);
console.log(`     索引 URL：${indexUrl}`);
