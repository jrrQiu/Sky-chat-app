// features/chat/services/chat.service.ts
import { nanoid } from 'nanoid'
import { useChatStore } from '../store/chat.store'
import { SSEParser } from '../utils/sse-parser'
import type { Message } from '../types/chat'

// 用于保存当前请求的控制器，方便用户中途点击“停止生成”
let streamAbortController: AbortController | null = null

export const ChatService = {
  /**
   * 中断当前正在生成的流
   */
  abortStream() {
    if (streamAbortController) {
      streamAbortController.abort()
      streamAbortController = null
    }
    useChatStore.getState().stopStreaming()
  },

  /**
   * 发送消息并处理流式响应
   */
  async sendMessage(content: string, conversationId?: string) {
    const store = useChatStore.getState()
    
    // 防抖：如果正在发送，则忽略新的发送请求
    if (store.isSendingMessage) return

    // 1. 构造用户消息并立刻上屏
    const userMessage: Message = {
      id: nanoid(),
      role: 'user',
      content,
      conversationId,
      createdAt: Date.now(),
    }
    store.addMessage(userMessage)
    store.setSendingMessage(true)

    // 2. 预先构造 AI 的空回复消息，先占位（此时屏幕上会出现一个空的 AI 气泡）
    const assistantMessageId = nanoid()
    const assistantMessage: Message = {
      id: assistantMessageId,
      role: 'assistant',
      content: '', // 初始内容为空
      conversationId,
      createdAt: Date.now(),
    }
    store.addMessage(assistantMessage)
    
    // 标记这条消息正在“流式生成”，UI 可以据此显示闪烁光标
    store.startStreaming(assistantMessageId)

    // 准备可以被中断的 Fetch 请求
    streamAbortController = new AbortController()

    try {
      // 3. 向我们写的后端 API 发起请求
      // 取出当前的所有历史消息作为上下文发给大模型
      const currentMessages = useChatStore.getState().messages
      
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: currentMessages.map(m => ({ role: m.role, content: m.content })),
          conversationId,
        }),
        signal: streamAbortController.signal,
      })

      if (!response.ok || !response.body) {
        throw new Error('网络请求失败')
      }

      // 4. 接管水管 (ReadableStream)
      const reader = response.body.getReader()

      // 5.开始源源不断地解析水流
      await SSEParser.parseStream(reader, {
        onData: (data) => {
          // 这个回调会被疯狂触发
          
          // 如果是文本增量，就追加到对应的 AI 消息后面
          if (data.type === 'text' && data.textDelta) {
            useChatStore.getState().appendContent(assistantMessageId, data.textDelta)
          }
        },
        onError: (error) => {
          console.error('流解析发生错误:', error)
          useChatStore.getState().updateMessage(assistantMessageId, {
            content: useChatStore.getState().messages.find(m => m.id === assistantMessageId)?.content + '\n\n*(网络连接中断)*'
          })
        },
        onComplete: () => {
          // 流结束
          console.log('AI 生成完毕')
        }
      })

    } catch (error: any) {
      if (error.name === 'AbortError') {
        console.log('用户主动停止了生成')
      } else {
        console.error('发送消息失败:', error)
        // 给个兜底的错误提示
        useChatStore.getState().updateMessage(assistantMessageId, {
          content: '抱歉，服务出现了一些问题，请稍后再试。'
        })
      }
    } finally {
      // 6. 收尾工作，重置状态
      store.setSendingMessage(false)
      store.stopStreaming()
      streamAbortController = null
    }
  }
}