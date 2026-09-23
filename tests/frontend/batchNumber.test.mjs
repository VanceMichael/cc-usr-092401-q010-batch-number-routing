/**
 * 前端批次号契约的 Node 回归测试：规范化与严格编码。
 * 编码向量固化了 Python urllib.parse.quote(safe='') 的输出，
 * 保证 JS/Python 生成的规范地址字面完全一致。
 * 运行：node --test tests/frontend/batchNumber.test.mjs
 * （TS 源通过 esbuild 转译，与生产构建同编译器）
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { readFileSync } from 'node:fs';

const here = dirname(fileURLToPath(import.meta.url));
const frontendDir = join(here, '..', '..', 'frontend');
const srcPath = join(frontendDir, 'src', 'utils', 'batchNumber.ts');

// 从 frontend 的 node_modules 加载 esbuild（与生产构建同一编译器）
const require = createRequire(join(frontendDir, 'index.html'));
const { build } = require('esbuild');

// esbuild 把 TS 转成 ESM 字符串，再通过 data: URL 导入
const bundled = await build({
  stdin: { contents: readFileSync(srcPath, 'utf-8'), sourcefile: 'batchNumber.ts', loader: 'ts' },
  bundle: false,
  write: false,
  format: 'esm',
});
const mod = await import('data:text/javascript;base64,' + Buffer.from(bundled.outputFiles[0].text).toString('base64'));
const {
  canonicalizeBatchNumber,
  BatchNumberFormatError,
  strictEncodeURIComponent,
  tracePath,
  extractNFromUrl,
} = mod;

test('NFC 归一化 + 首尾空白（含全角空格）', () => {
  const nfd = 'Å'.normalize('NFD') + 'ngström';
  assert.equal(canonicalizeBatchNumber('  ' + 'Ångström' + '\t'), 'Ångström');
  assert.equal(canonicalizeBatchNumber('　批次01　'), '批次01');
  assert.equal(canonicalizeBatchNumber(nfd), 'Ångström');
});

test('大小写敏感，不合并原本不同的号', () => {
  assert.equal(canonicalizeBatchNumber('ABC'), 'ABC');
  assert.equal(canonicalizeBatchNumber('abc'), 'abc');
});

test('斜杠/百分号/空格/中文均合法', () => {
  assert.equal(canonicalizeBatchNumber('B/2024 批次%x'), 'B/2024 批次%x');
});

test('非法：空、纯空白、控制字符、超长', () => {
  assert.throws(() => canonicalizeBatchNumber(''), BatchNumberFormatError);
  assert.throws(() => canonicalizeBatchNumber('   '), BatchNumberFormatError);
  assert.throws(() => canonicalizeBatchNumber('AB\u0000'), BatchNumberFormatError);
  assert.throws(() => canonicalizeBatchNumber('a'.repeat(51)), BatchNumberFormatError);
  // 零宽字符（Cf）也算控制类
  assert.throws(() => canonicalizeBatchNumber('ab\u200bcd'), BatchNumberFormatError);
  // 隔离在合理长度内：50 字符合法
  assert.equal(canonicalizeBatchNumber('a'.repeat(50)), 'a'.repeat(50));
});

test('严格编码与 Python quote(safe=) 完全对齐', () => {
  const vectors = {
    'B-2024/001': 'B-2024%2F001',
    '2024 秋季 批次A': '2024%20%E7%A7%8B%E5%AD%A3%20%E6%89%B9%E6%AC%A1A',
    'ABC': 'ABC',
    "x!y'z(a)b*c": 'x%21y%27z%28a%29b%2Ac',
    'Ångström-01': '%C3%85ngstr%C3%B6m-01',
    '100%done': '100%25done',
    'a+b=c&d=e': 'a%2Bb%3Dc%26d%3De',
  };
  for (const [input, expected] of Object.entries(vectors)) {
    assert.equal(strictEncodeURIComponent(input), expected, input);
  }
});

test('tracePath 生成唯一规范地址', () => {
  assert.equal(tracePath('B-2024/001'), '/trace?n=B-2024%2F001');
  assert.equal(
    tracePath('2024 秋季'),
    '/trace?n=2024%20%E7%A7%8B%E5%AD%A3'
  );
  assert.throws(() => tracePath('  '), BatchNumberFormatError);
});

test('extractNFromUrl 解码回真实批次号', () => {
  assert.equal(
    extractNFromUrl('http://host/api/batches/by-number?src=qr&n=B-2024%2F001'),
    'B-2024/001'
  );
  assert.equal(extractNFromUrl('/trace?n=%E6%89%B9%E6%AC%A1'), '批次');
  assert.equal(extractNFromUrl('/trace'), null);
});
