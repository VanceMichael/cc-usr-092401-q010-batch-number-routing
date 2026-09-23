/**
 * 批次追溯链接的唯一构造规则（前端侧）。
 *
 * 分享链接、直接刷新、站内跳转必须共用本函数，保证任何入口都得到同一个
 * 规范地址：/trace?batch_number=<encodeURIComponent(批次号)>
 *
 * 批次号只允许出现在 query 中：encodeURIComponent 以 UTF-8 百分号编码，
 * 斜杠（/ -> %2F）、空格（%20）、中文等字符在浏览器与服务端得到一致结果，
 * 不会被误切成路径段。
 */

export const TRACE_PATH = '/trace';

export function buildBatchTracePath(
  batchNumber: string,
  extraParams?: Record<string, string>,
): string {
  // 手工拼接而非 URLSearchParams：后者会把空格编成 '+'，规范地址固定 %20。
  let search = `batch_number=${encodeURIComponent(batchNumber)}`;
  if (extraParams) {
    for (const [key, value] of Object.entries(extraParams)) {
      search += `&${encodeURIComponent(key)}=${encodeURIComponent(value)}`;
    }
  }
  return `${TRACE_PATH}?${search}`;
}

/** 生成可外发给客服/客户的完整追溯链接。 */
export function buildBatchTraceShareUrl(batchNumber: string): string {
  return `${window.location.origin}${buildBatchTracePath(batchNumber)}`;
}

/** 读取地址中的批次号参数（浏览器已完成百分号解码）。 */
export function readBatchNumberFromLocation(search: string): string | null {
  return new URLSearchParams(search).get('batch_number');
}
