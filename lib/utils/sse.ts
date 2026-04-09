// lib/utils/sse.ts

/**
 * 解析 SSE 行数据
 * @param line - SSE 行字符串
 * @returns 解析后的数据字符串，如果是 [DONE] 或无效行则返回 null
 */
export function parseSSELine(line: string): string | null {
  if (!line.startsWith('data: ')) return null
  const data = line.slice(6).trim()
  if (data === '[DONE]') return null
  return data
}

/**
 * 分割 SSE buffer（核心：解决断包问题）
 * 按换行符分割，保留最后一行（可能是不完整的 JSON，留给下一次拼接）
 * @param buffer - 累积的 buffer 字符串
 * @returns lines: 完整的行数组, remaining: 剩余的不完整行
 */
export function splitSSEBuffer(buffer: string): { lines: string[]; remaining: string } {
  // 注意：网络流的换行符可能是 \n 也可能是 \r\n，这里统一处理
  const lines = buffer.split(/\r?\n/)
  // 弹出最后一行作为 remaining。如果最后一行是空字符串，说明上一行正好以换行符结尾，是完整包
  const remaining = lines.pop() || ''
  return { lines, remaining }
}