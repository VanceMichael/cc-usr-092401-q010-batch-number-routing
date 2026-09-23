/**
 * 批次号契约的前端唯一实现，规则与后端 app/services/batch_numbers.py 完全一致：
 *   1. Unicode 规范化为 NFC；
 *   2. 去除首尾空白（trim，含全角空格 U+3000）；
 *   3. 大小写敏感 —— "ABC" 与 "abc" 是两个不同批次号；
 *   4. 不允许控制字符；规范化后长度 1..50（按 Unicode 码点计）；
 *   5. "/"、"%"、空格、中文允许出现，但在地址里一律百分号编码
 *      （"/"→%2F、空格→%20），编码表与 Python urllib quote(safe='') 对齐，
 *      保证分享链接、直接刷新、站内跳转得到同一个规范地址。
 */

export const MAX_BATCH_NUMBER_LENGTH = 50;

export class BatchNumberFormatError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'BatchNumberFormatError';
  }
}

// 与 Python unicodedata.category(ch) 首字母 'C' 大致对齐的码点区间：
// Cc 控制字符、Cf 格式字符、Cs 代理、Co 私用区（Cn 未分配码点无法由正常文本输入）。
function isControlCodePoint(cp: number): boolean {
  return (
    (cp >= 0x0000 && cp <= 0x001f) ||
    (cp >= 0x007f && cp <= 0x009f) || // Cc
    cp === 0x00ad ||
    (cp >= 0x0600 && cp <= 0x0605) ||
    cp === 0x061c ||
    cp === 0x06dd ||
    cp === 0x0670 ||
    cp === 0x070f ||
    cp === 0x08e2 ||
    cp === 0x180e ||
    (cp >= 0x200b && cp <= 0x200f) ||
    (cp >= 0x202a && cp <= 0x202e) ||
    (cp >= 0x2060 && cp <= 0x2064) ||
    (cp >= 0x2066 && cp <= 0x206f) ||
    cp === 0xfeff ||
    (cp >= 0xfff9 && cp <= 0xfffb) ||
    cp === 0x110bd ||
    cp === 0x110cd ||
    (cp >= 0xe0000 && cp <= 0xe0001) ||
    (cp >= 0xe0020 && cp <= 0xe007f) || // Cf
    (cp >= 0xd800 && cp <= 0xdfff) || // Cs
    (cp >= 0xe000 && cp <= 0xf8ff) // Co
  );
}

function containsControlChar(text: string): boolean {
  return Array.from(text).some((ch) => isControlCodePoint(ch.codePointAt(0) as number));
}

export function canonicalizeBatchNumber(raw: string): string {
  if (raw === null || raw === undefined) {
    throw new BatchNumberFormatError('批次号不能为空');
  }
  const text = String(raw).normalize('NFC').trim();
  if (!text) {
    throw new BatchNumberFormatError('批次号不能为空');
  }
  if (Array.from(text).length > MAX_BATCH_NUMBER_LENGTH) {
    throw new BatchNumberFormatError(`批次号长度不能超过 ${MAX_BATCH_NUMBER_LENGTH} 个字符`);
  }
  if (containsControlChar(text)) {
    throw new BatchNumberFormatError('批次号不能包含控制字符');
  }
  return text;
}

/** 是否需要归一化跳转（与后端 needs_redirect 语义一致）。 */
export function needsCanonicalRedirect(raw: string): boolean {
  try {
    return String(raw) !== canonicalizeBatchNumber(raw);
  } catch {
    return false;
  }
}

/**
 * 严格百分号编码：编码表对齐 Python urllib.parse.quote(safe='')，
 * 比 encodeURIComponent 额外编码 ! ' ( ) * ，使前后端生成的地址字面完全一致。
 */
export function strictEncodeURIComponent(value: string): string {
  return encodeURIComponent(value).replace(
    /[!'()*]/g,
    (ch) => '%' + ch.charCodeAt(0).toString(16).toUpperCase()
  );
}

/** axios paramsSerializer：查询参数全部严格编码。 */
export function strictParamsSerializer(
  params: Record<string, string | number | boolean | undefined | null>
): string {
  return Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null)
    .map(([k, v]) => `${strictEncodeURIComponent(k)}=${strictEncodeURIComponent(String(v))}`)
    .join('&');
}

/** 站内追溯页规范地址：/trace?n=... */
export function tracePath(raw: string): string {
  return `/trace?n=${strictEncodeURIComponent(canonicalizeBatchNumber(raw))}`;
}

/** 后端按批次号查询接口的规范地址。 */
export function batchByNumberApiPath(raw: string): string {
  return `/batches/by-number?n=${strictEncodeURIComponent(canonicalizeBatchNumber(raw))}`;
}

/** 后端按批次号追溯接口的规范地址。 */
export function traceByNumberApiPath(raw: string): string {
  return `/analysis/trace-by-number?n=${strictEncodeURIComponent(canonicalizeBatchNumber(raw))}`;
}

/**
 * 从浏览器地址栏或 XHR responseURL 中提取 n 参数（解码后的值）。
 * 用于 301 别名归一后把地址栏替换为当前规范批次号。提取失败返回 null。
 */
export function extractNFromUrl(url: string | undefined | null): string | null {
  if (!url) return null;
  const q = url.split('?')[1];
  if (!q) return null;
  for (const pair of q.split('&')) {
    const eq = pair.indexOf('=');
    const k = decodeURIComponent(eq === -1 ? pair : pair.slice(0, eq));
    if (k === 'n') {
      return eq === -1 ? '' : decodeURIComponent(pair.slice(eq + 1));
    }
  }
  return null;
}
