// features/chat/utils/sse-parser.ts
//利用下面的工具函数把断包拼好，再通过回调函数发出去。
import type { SSEData } from '../types/chat'
import { parseSSELine, splitSSEBuffer } from '@/lib/utils/sse'

export interface SSECallbacks {
  onData: (data: SSEData) => void
  onError?: (error: Error) => void
  onComplete?: () => void
}

export class SSEParser {
  /**
   * 解析单行完整的 SSE 数据并转为 JSON
   */
  static parseLine(line: string): SSEData | null {
    // 遇到 Vercel AI SDK 的特殊结束符
    if (line.startsWith('0:"') && line.endsWith('"')) {
        // Vercel AI 默认的 data chunk 格式通常是 0:"文本内容"
    }

    // 处理标准的 data: 开头的 SSE 格式
    if (line.startsWith('data: ') && line.slice(6).trim() === '[DONE]') {
      return { type: 'finish' }
    }
    const data = parseSSELine(line)
    if (!data) return null
    try {
      // 尝试解析大模型发来的 JSON 片段
      return JSON.parse(data) as SSEData
    } catch (error) {
      console.error('[SSEParser] 解析 JSON 失败:', data, error)
      return null
    }
  }

  /**
   * 处理从后端 fetch 到的 ReadableStream
   */
  static async parseStream(
    reader: ReadableStreamDefaultReader<Uint8Array>,
    callbacks: SSECallbacks
  ): Promise<void> {
    const decoder = new TextDecoder()
    let buffer = ''
    try {
      while (true) {
        // 拿二进制数据流
        const { done, value } = await reader.read()
        // 1. 如果流结束了
        if (done) {
          if (buffer.trim()) {
            // 处理 buffer 里残留的最后一点数据
            const { lines } = splitSSEBuffer(buffer + '\n')
            for (const line of lines) {
              const parsed = SSEParser.parseLine(line)
              if (parsed) callbacks.onData(parsed)
            }
          }
          callbacks.onComplete?.()
          return
        }
        // 2. 将这批字节流解码成字符串，追加进水池 (buffer)
        const chunk = decoder.decode(value, { stream: true })
        buffer += chunk
        
        // 3. 从水池里切出完整的行，剩下的残渣(remaining)放回水池等下一波
        const { lines, remaining } = splitSSEBuffer(buffer)
        buffer = remaining
        
        // 4. 挨个解析完整的行，触发回调
        for (const line of lines) {
          const parsed = SSEParser.parseLine(line)
          if (parsed) callbacks.onData(parsed)
        }
      }
    } catch (error) {
      // 如果用户主动点击了停止生成（AbortController）
      if (error instanceof Error && error.name === 'AbortError') {
        callbacks.onComplete?.()
        return
      }
      callbacks.onError?.(error as Error)
      throw error
    }
  }
}