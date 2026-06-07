/**
 * Chat Service - 业务逻辑
 *
 * 处理消息发送、加载、流式解析等
 * 不依赖 React，纯业务逻辑
 */

import { nanoid } from 'nanoid'
import { useChatStore } from '@/features/chat/store/chat.store'
import { SSEParser } from '@/features/chat/utils/sse-parser'
import type { Message, FileAttachment } from '@/features/chat/types/chat'

// 用于流缓冲更新的类（原版 StreamBuffer 逻辑内联简化，避免依赖缺失）
class StreamBuffer {
  private buffer: string = ''
  private timer: NodeJS.Timeout | null = null
  private onFlush: (content: string) => void

  constructor({ onFlush }: { onFlush: (content: string) => void }) {
    this.onFlush = onFlush
  }

  append(chunk: string) {
    this.buffer += chunk
    if (!this.timer) {
      this.timer = setTimeout(() => this.flush(), 30) // 30ms 节流更新
    }
  }

  flush() {
    if (this.timer) {
      clearTimeout(this.timer)
      this.timer = null
    }
    if (this.buffer.length > 0) {
      this.onFlush(this.buffer)
      this.buffer = ''
    }
  }

  forceFlush() {
    this.flush()
  }

  destroy() {
    if (this.timer) clearTimeout(this.timer)
    this.buffer = ''
  }
}

// 用于取消请求
let loadAbortController: AbortController | null = null
let streamAbortController: AbortController | null = null

export const ChatService = {
  /**
   * 中断当前流式请求
   */
  abortStream(): void {
    if (streamAbortController) {
      streamAbortController.abort()
      streamAbortController = null
    }
    
    // 更新状态机：将当前流式消息的状态转为 idle
    const store = useChatStore.getState()
    const messageId = store.streamingMessageId
    if (messageId) {
      // 取消所有正在运行的工具
      const messageState = store.messageStates.get(messageId)
      if (messageState) {
        for (const [toolCallId, tool] of messageState.activeTools) {
          if (tool.state === 'running') {
            store.cancelTool(messageId, toolCallId)
            // 通知后端取消工具（这里暂不强求后端一定存在）
            fetch('/api/chat/cancel-tool', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ toolCallId }),
            }).catch(() => {})
          }
        }
      }
      
      // 状态机转到 idle
      store.transitionPhase(messageId, { type: 'COMPLETE' })
      store.updateMessage(messageId, { displayState: 'idle' })
    }
  },

  /**
   * 取消指定工具的执行
   */
  async cancelTool(messageId: string, toolCallId: string, abortStream = false): Promise<boolean> {
    try {
      const response = await fetch('/api/chat/cancel-tool', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ toolCallId }),
      })
      const data = await response.json()
      
      if (data.success) {
        const store = useChatStore.getState()
        store.cancelTool(messageId, toolCallId)
        
        if (abortStream) {
          this.abortStream()
        }
      }
      return data.success
    } catch (e) {
      console.error('[ChatService] cancelTool failed:', e)
      return false
    }
  },

  /**
   * 加载会话消息
   */
  async loadMessages(conversationId: string): Promise<void> {
    const store = useChatStore.getState()

    if (store.isSendingMessage) {
      console.log('[ChatService] Skipping loadMessages - sending in progress')
      return
    }

    if (store.streamingMessageId) {
      this.abortStream()
    }

    loadAbortController?.abort()
    loadAbortController = new AbortController()

    const cached = store.getCachedMessages(conversationId)
    const hasCache = cached && cached.length > 0

    if (!hasCache) {
      store.setLoadingMessages(true, conversationId)
    } else {
      store.setMessages(cached)
    }

    try {
      const response = await fetch(`/api/conversations/${conversationId}/messages`, {
        signal: loadAbortController.signal
      })
      
      if (!response.ok) {
        if (response.status === 404) {
          console.warn('[ChatService] Conversation not found, redirecting to home')
          window.location.href = '/'
          return
        }
        throw new Error('Failed to load messages')
      }

      const data = await response.json()
      const messages = data.messages || []

      // 去重
      const unique = messages.filter(
        (msg: Message, i: number, arr: Message[]) => arr.findIndex((m) => m.id === msg.id) === i
      ) as Message[]

      store.cacheMessages(conversationId, unique)
      store.setMessages(unique)
    } catch (e) {
      if ((e as Error).name === 'AbortError') return
      console.error('[ChatService] loadMessages failed:', e)
    } finally {
      loadAbortController = null
      store.setLoadingMessages(false)
    }
  },

  /**
   * 发送消息
   */
  async sendMessage(
    conversationId: string,
    content: string,
    options: {
      createUserMessage?: boolean
      attachments?: FileAttachment[]
      enableImageGeneration?: boolean
      imageConfig?: { prompt: string; negative_prompt?: string; image_size: string }
    } = {}
  ): Promise<void> {
    const { createUserMessage = true, attachments, enableImageGeneration, imageConfig } = options
    const store = useChatStore.getState()

    if (store.isSendingMessage) {
      console.log('[ChatService] Already sending, skipping')
      return
    }
    store.setSendingMessage(true)

    const userMessageId = createUserMessage ? nanoid() : undefined
    const aiMessageId = nanoid()

    // 添加用户消息
    if (createUserMessage && userMessageId) {
      store.addMessage({
        id: userMessageId,
        role: 'user',
        content,
        attachments,
      })
    }

    // 添加 AI 占位消息
    store.addMessage({
      id: aiMessageId,
      role: 'assistant',
      content: '',
      thinking: '',
      displayState: 'waiting',
    })

    try {
      streamAbortController = new AbortController()

      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content,
          conversationId,
          model: store.selectedModel,
          enableThinking: store.enableThinking,
          enableWebSearch: store.enableWebSearch,
          enableImageGeneration,
          imageConfig,
          thinkingBudget: 4096,
          userMessageId,
          aiMessageId,
          attachments,
        }),
        signal: streamAbortController.signal,
      })

      if (!response.ok) throw new Error(`API error: ${response.status}`)

      // 同步标题
      const newTitle = response.headers.get('X-Conversation-Title')
      if (newTitle) {
        const decodedTitle = decodeURIComponent(newTitle)
        try {
          const { useConversationStore } = await import('@/features/conversation/store/conversation-store')
          useConversationStore.setState((state) => ({
            conversations: state.conversations.map((c) =>
              c.id === conversationId ? { ...c, title: decodedTitle } : c
            )
          }))
        } catch(e) {}
      }

      const reader = response.body?.getReader()
      if (!reader) throw new Error('No reader')

      await this.handleStream(reader, aiMessageId)
    } catch (e) {
      if ((e as Error).name === 'AbortError') {
        store.updateMessage(aiMessageId, { displayState: 'idle' })
        return
      }
      console.error('[ChatService] sendMessage failed:', e)
      store.updateMessage(aiMessageId, { hasError: true, displayState: 'error' })
      store.stopStreaming()
    } finally {
      streamAbortController = null
      store.setSendingMessage(false)
      // 更新消息缓存
      const currentMessages = useChatStore.getState().messages
      useChatStore.getState().cacheMessages(conversationId, currentMessages)
    }
  },

  /**
   * 处理 SSE 流
   */
  async handleStream(
    reader: ReadableStreamDefaultReader<Uint8Array>,
    messageId: string
  ): Promise<void> {
    const store = useChatStore.getState()
    store.initMessageState(messageId)

    const thinkingBuffer = new StreamBuffer({
      onFlush: (content) => useChatStore.getState().appendThinking(messageId, content),
    })

    const answerBuffer = new StreamBuffer({
      onFlush: (content) => useChatStore.getState().appendContent(messageId, content),
    })

    try {
      await SSEParser.parseStream(reader, {
        onData: (data) => {
          const s = useChatStore.getState()

          if (data.type === 'thinking' && data.content) {
            if (s.streamingPhase !== 'thinking') {
              s.startStreaming(messageId, 'thinking')
              s.transitionPhase(messageId, { type: 'START_THINKING' })
              s.updateMessage(messageId, { displayState: 'streaming' })
            }
            thinkingBuffer.append(data.content)
          } else if (data.type === 'answer' && data.content) {
            if (s.streamingPhase !== 'answer') {
              s.startStreaming(messageId, 'answer')
              s.transitionPhase(messageId, { type: 'START_ANSWERING' })
              s.updateMessage(messageId, { displayState: 'streaming' })
            }
            answerBuffer.append(data.content)
          } else if (data.type === 'tool_call') {
            const toolCallId = data.toolCallId || nanoid()
            s.transitionPhase(messageId, {
              type: 'START_TOOL_CALL',
              toolCallId,
              name: data.name || 'unknown',
              args: { query: data.query, prompt: data.prompt },
            })
            
            const msg = s.messages.find((m) => m.id === messageId)
            const invocations = msg?.toolInvocations || []
            const newInvocation = {
              toolCallId,
              name: data.name || 'unknown',
              state: 'running' as const,
              args: {
                query: data.query,
                prompt: data.prompt,
              },
            }
            s.updateMessage(messageId, {
              toolInvocations: [...invocations, newInvocation],
              displayState: 'streaming',
            })
          } else if (data.type === 'tool_progress') {
            if (data.toolCallId && data.progress !== undefined) {
              s.transitionPhase(messageId, {
                type: 'TOOL_PROGRESS',
                toolCallId: data.toolCallId,
                progress: data.progress,
                estimatedTime: data.estimatedTime,
              })
              s.updateToolProgress(messageId, data.toolCallId, data.progress, data.estimatedTime)
            }
          } else if (data.type === 'tool_result') {
            s.transitionPhase(messageId, {
              type: 'TOOL_COMPLETE',
              toolCallId: data.toolCallId || '',
              success: data.success ?? false,
              result: {
                imageUrl: data.imageUrl,
                resultCount: data.resultCount,
                sources: data.sources,
              },
            })

            const msg = s.messages.find((m) => m.id === messageId)
            const invocations = msg?.toolInvocations || []
            const updatedInvocations = invocations.map((inv) => {
              const isMatch = data.toolCallId
                ? inv.toolCallId === data.toolCallId
                : inv.name === data.name && inv.state === 'running'
              if (isMatch) {
                return {
                  ...inv,
                  state: data.success ? ('completed' as const) : ('failed' as const),
                  result: {
                    success: data.success ?? false,
                    imageUrl: data.imageUrl,
                    resultCount: data.resultCount,
                    sources: data.sources,
                    width: data.width,
                    height: data.height,
                  },
                }
              }
              return inv
            })

            if (data.name === 'generate_image' && data.success && data.imageUrl) {
              const imageData = JSON.stringify({
                url: data.imageUrl,
                alt: invocations.find((inv) => inv.toolCallId === data.toolCallId)?.args?.prompt || '生成的图片',
                width: data.width || 512,
                height: data.height || 512,
              })
              answerBuffer.append(`\n\`\`\`image\n${imageData}\n\`\`\`\n`)
            }

            s.updateMessage(messageId, {
              toolInvocations: updatedInvocations,
            })
          } else if (data.type === 'complete') {
            thinkingBuffer.forceFlush()
            answerBuffer.forceFlush()
            s.transitionPhase(messageId, { type: 'COMPLETE' })
            s.stopStreaming()
            s.updateMessage(messageId, { displayState: 'idle' })
          }
        },
        onError: (error) => {
          console.error('[ChatService] stream error:', error)
          thinkingBuffer.forceFlush()
          answerBuffer.forceFlush()
          const s = useChatStore.getState()
          s.transitionPhase(messageId, { type: 'ERROR', message: error.message })
          s.updateMessage(messageId, { hasError: true, displayState: 'error' })
          s.stopStreaming()
        },
        onComplete: () => {
          thinkingBuffer.forceFlush()
          answerBuffer.forceFlush()
          const s = useChatStore.getState()
          s.stopStreaming()
          s.updateMessage(messageId, { displayState: 'idle' })
        },
      })
    } finally {
      thinkingBuffer.destroy()
      answerBuffer.destroy()
    }
  },

  /**
   * 重试消息
   */
  async retryMessage(conversationId: string, messageId: string): Promise<void> {
    const store = useChatStore.getState()

    if (store.streamingMessageId) {
      store.stopStreaming('user_retry')
    }

    const index = store.messages.findIndex((m) => m.id === messageId)
    if (index === -1) return

    const message = store.messages[index]
    if (message.role !== 'assistant') return

    const removed = store.removeMessagesFrom(index)
    const idsToDelete = removed.map((m) => m.id)

    const lastUserMsg = [...store.messages].reverse().find((m) => m.role === 'user')
    if (!lastUserMsg) return

    if (idsToDelete.length > 0) {
      fetch('/api/messages/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messageIds: idsToDelete }),
      }).catch(console.error)
    }

    await this.sendMessage(conversationId, lastUserMsg.content, { 
      createUserMessage: false,
    })
  },

  /**
   * 编辑并重发
   */
  async editAndResend(
    conversationId: string,
    messageId: string,
    newContent: string
  ): Promise<void> {
    const store = useChatStore.getState()
    const index = store.messages.findIndex((m) => m.id === messageId)

    if (index === -1) return
    if (store.messages[index].role !== 'user') return

    const removed = store.removeMessagesFrom(index)
    const idsToDelete = removed.map((m) => m.id)

    if (idsToDelete.length > 0) {
      fetch('/api/messages/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messageIds: idsToDelete }),
      }).catch(console.error)
    }

    await this.sendMessage(conversationId, newContent, { createUserMessage: true })
  },
}