/**
 * SSE 消息解析器
 *
 * 职责：
 * 1. 解析 SSE 流
 * 2. 区分 thinking / answer / tool_calls
 * 3. 返回标准化的数据
 */

import type { SSEData } from '@/features/chat/types/chat'
import { parseSSELine } from '@/lib/utils/sse'

export interface SSECallbacks {
  onData: (data: SSEData, eventId?: string) => void | Promise<void>
  onError?: (error: Error) => void | Promise<void>
  onComplete?: () => void | Promise<void>
}

export class SSEParser {
  /**
   * 解析一个完整 SSE 事件块，保留 id 字段用于续传去重。
   */
  static parseEvent(block: string): { data: SSEData; id?: string } | null {
    const dataLines: string[] = []
    let eventId: string | undefined

    for (const rawLine of block.split(/\r?\n/)) {
      if (!rawLine || rawLine.startsWith(':')) continue

      const separatorIndex = rawLine.indexOf(':')
      const field =
        separatorIndex === -1 ? rawLine : rawLine.slice(0, separatorIndex)
      let value = separatorIndex === -1 ? '' : rawLine.slice(separatorIndex + 1)
      if (value.startsWith(' ')) value = value.slice(1)

      if (field === 'id') {
        eventId = value
      } else if (field === 'data') {
        dataLines.push(value)
      }
    }

    if (dataLines.length === 0) return null

    const data = dataLines.join('\n').trim()
    if (!data) return null

    if (data === '[DONE]') {
      return { data: { type: 'complete' }, id: eventId }
    }

    try {
      return {
        data: JSON.parse(data) as SSEData,
        id: eventId,
      }
    } catch (error) {
      console.error('[SSEParser] Failed to parse SSE data:', data, error)
      return null
    }
  }

  /**
   * 解析单行 SSE 数据
   */
  static parseLine(line: string): SSEData | null {
    if (line.startsWith('data: ') && line.slice(6).trim() === '[DONE]') {
      return { type: 'complete' }
    }

    const data = parseSSELine(line)
    if (!data) return null

    try {
      return JSON.parse(data) as SSEData
    } catch (error) {
      console.error('[SSEParser] Failed to parse SSE data:', data, error)
      return null
    }
  }

  /**
   * 处理 SSE 流，使用回调函数
   */
  static async parseStream(
    reader: ReadableStreamDefaultReader<Uint8Array>,
    callbacks: SSECallbacks
  ): Promise<void> {
    const decoder = new TextDecoder()
    let buffer = ''

    const emitCompleteBlocks = async () => {
      let boundary = buffer.match(/\r?\n\r?\n/)

      while (boundary?.index !== undefined) {
        const block = buffer.slice(0, boundary.index)
        buffer = buffer.slice(boundary.index + boundary[0].length)

        const parsed = SSEParser.parseEvent(block)
        if (parsed) {
          await callbacks.onData(parsed.data, parsed.id)
        }

        boundary = buffer.match(/\r?\n\r?\n/)
      }
    }

    try {
      while (true) {
        const { done, value } = await reader.read()

        if (done) {
          buffer += decoder.decode()
          await emitCompleteBlocks()

          if (buffer.trim()) {
            const parsed = SSEParser.parseEvent(buffer)
            if (parsed) {
              await callbacks.onData(parsed.data, parsed.id)
            }
          }

          await callbacks.onComplete?.()
          return
        }

        buffer += decoder.decode(value, { stream: true })
        await emitCompleteBlocks()
      }
    } catch (error) {
      await callbacks.onError?.(error as Error)
      throw error
    }
  }
}
