/**
 * Chat Service - 业务逻辑
 *
 * 处理消息发送、加载、流式解析、断线重连和主动取消。
 */

import { nanoid } from 'nanoid'
import { apiFetch, apiFetchWithSignal } from '@/lib/api/client'
import { useChatStore } from '@/features/chat/store/chat.store'
import { SSEParser } from '@/features/chat/utils/sse-parser'
import type {
  FileAttachment,
  Message,
  SSEData,
  ToolInvocation,
} from '@/features/chat/types/chat'

class StreamBuffer {
  private buffer = ''
  private timer: ReturnType<typeof setTimeout> | null = null
  private readonly onFlush: (content: string) => void

  constructor({ onFlush }: { onFlush: (content: string) => void }) {
    this.onFlush = onFlush
  }

  append(chunk: string) {
    this.buffer += chunk
    if (!this.timer) {
      this.timer = setTimeout(() => this.flush(), 30)
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

class ChatRequestError extends Error {
  constructor(
    message: string,
    readonly retryable: boolean,
    readonly status?: number
  ) {
    super(message)
    this.name = 'ChatRequestError'
  }
}

interface ActiveStreamRequest {
  controller: AbortController
  messageId: string
  runId: string
  userAborted: boolean
}

interface StreamCursor {
  runId: string | null
  lastSeq: number
  receivedEvent: boolean
  completed: boolean
  terminal: boolean
  cancelled: boolean
}

const MAX_CONNECT_ATTEMPTS = 4
const REQUEST_TIMEOUT_MS = 30_000
const BASE_RETRY_DELAY_MS = 600
const MAX_RETRY_DELAY_MS = 8_000

let loadAbortController: AbortController | null = null
let activeStreamRequest: ActiveStreamRequest | null = null

function createAbortError(): Error {
  const error = new Error('Aborted')
  error.name = 'AbortError'
  return error
}

function createRunId(): string {
  if (
    typeof crypto !== 'undefined' &&
    typeof crypto.randomUUID === 'function'
  ) {
    return `run_${crypto.randomUUID()}`
  }
  return `run_${nanoid()}`
}

function createIdempotencyKey(runId: string): string {
  return `idem_${runId}`
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError'
}

function isRetryableStatus(status: number): boolean {
  return (
    status === 408 ||
    status === 429 ||
    status === 500 ||
    status === 502 ||
    status === 503 ||
    status === 504
  )
}

function getRetryDelay(attempt: number): number {
  const exponential = Math.min(
    MAX_RETRY_DELAY_MS,
    BASE_RETRY_DELAY_MS * 2 ** Math.max(0, attempt - 1)
  )
  const jitterFactor = 0.5 + Math.random() * 0.5
  return Math.round(exponential * jitterFactor)
}

function wait(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(createAbortError())
      return
    }

    let timer: ReturnType<typeof setTimeout> | null = null
    const onAbort = () => {
      if (timer) clearTimeout(timer)
      signal.removeEventListener('abort', onAbort)
      reject(createAbortError())
    }

    signal.addEventListener('abort', onAbort, { once: true })
    timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
  })
}

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const data = await response.json()
    if (data && typeof data.error === 'string') return data.error
  } catch {
    // Fall back to the status text.
  }
  return response.statusText || `HTTP ${response.status}`
}

async function toHttpError(response: Response): Promise<ChatRequestError> {
  return new ChatRequestError(
    await readErrorMessage(response),
    isRetryableStatus(response.status),
    response.status
  )
}

async function fetchWithTimeout(
  url: string,
  init: RequestInit,
  signal: AbortSignal,
  timeoutMs = REQUEST_TIMEOUT_MS
): Promise<Response> {
  if (signal.aborted) throw createAbortError()

  const controller = new AbortController()
  let timedOut = false

  const onAbort = () => controller.abort()
  signal.addEventListener('abort', onAbort, { once: true })

  const timeout = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)

  try {
    return await apiFetchWithSignal(url, {
      ...init,
    }, controller.signal)
  } catch (error) {
    if (signal.aborted) throw createAbortError()
    if (timedOut) {
      throw new ChatRequestError('请求超时', true)
    }
    if (error instanceof ChatRequestError) throw error
    throw new ChatRequestError(
      error instanceof Error ? error.message : '网络错误',
      true
    )
  } finally {
    clearTimeout(timeout)
    signal.removeEventListener('abort', onAbort)
  }
}

async function requestChatResponse(
  body: Record<string, unknown>,
  idempotencyKey: string,
  signal: AbortSignal
): Promise<Response> {
  let lastError: ChatRequestError | null = null

  for (let attempt = 1; attempt <= MAX_CONNECT_ATTEMPTS; attempt += 1) {
    try {
      const response = await fetchWithTimeout(
        '/v1/chat/stream',
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Idempotency-Key': idempotencyKey,
          },
          body: JSON.stringify(body),
        },
        signal
      )

      if (response.ok) return response

      const error = await toHttpError(response)
      if (!error.retryable || attempt === MAX_CONNECT_ATTEMPTS) {
        throw error
      }
      lastError = error
    } catch (error) {
      if (signal.aborted) throw createAbortError()

      const requestError =
        error instanceof ChatRequestError
          ? error
          : new ChatRequestError(
              error instanceof Error ? error.message : '网络错误',
              true
            )

      if (!requestError.retryable || attempt === MAX_CONNECT_ATTEMPTS) {
        throw requestError
      }
      lastError = requestError
    }

    await wait(getRetryDelay(attempt), signal)
  }

  throw lastError || new ChatRequestError('请求失败', true)
}

function extractSeq(data: SSEData, eventId?: string): number | null {
  const value = data.seq ?? (eventId ? Number(eventId) : Number.NaN)
  return Number.isFinite(value) ? Number(value) : null
}

function createCursor(runId: string | null = null, lastSeq = 0): StreamCursor {
  return {
    runId,
    lastSeq,
    receivedEvent: false,
    completed: false,
    terminal: false,
    cancelled: false,
  }
}

function applyToolResult(
  invocation: ToolInvocation,
  data: SSEData
): ToolInvocation {
  return {
    ...invocation,
    state: data.success ? 'completed' : 'failed',
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

export const ChatService = {
  /**
   * 用户主动停止生成。这个路径不会进入自动重连。
   */
  abortStream(): void {
    const active = activeStreamRequest

    if (active) {
      active.userAborted = true
      active.controller.abort()

      void apiFetch(`/v1/chat/runs/${encodeURIComponent(active.runId)}`, {
        method: 'DELETE',
      }).catch(() => {})
    }

    const store = useChatStore.getState()
    const messageId = store.streamingMessageId || active?.messageId

    if (messageId) {
      const messageState = store.messageStates.get(messageId)
      if (messageState) {
        for (const [toolCallId, tool] of messageState.activeTools) {
          if (tool.state === 'running') {
            store.cancelTool(messageId, toolCallId)
          }
        }
      }

      store.transitionPhase(messageId, { type: 'COMPLETE' })
      store.updateMessage(messageId, {
        displayState: 'idle',
        hasError: false,
      })
    }

    store.stopStreaming('user_stop')
  },

  /**
   * 取消指定工具的执行。
   */
  async cancelTool(
    messageId: string,
    toolCallId: string,
    abortStream = false
  ): Promise<boolean> {
    try {
      const store = useChatStore.getState()
      store.cancelTool(messageId, toolCallId)

      if (abortStream) {
        this.abortStream()
      }
      return true
    } catch (error) {
      console.error('[ChatService] cancelTool failed:', error)
      return false
    }
  },

  /**
   * 加载会话消息。
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
      const response = await apiFetch(
        `/v1/conversations/${conversationId}/messages`,
        {
          signal: loadAbortController.signal,
        }
      )

      if (!response.ok) {
        if (response.status === 404) {
          console.warn(
            '[ChatService] Conversation not found, redirecting to home'
          )
          window.location.href = '/'
          return
        }
        throw new Error('Failed to load messages')
      }

      const data = (await response.json()) as Message[]
      const messages = Array.isArray(data) ? data : []

      const unique = messages.filter(
        (message: Message, index: number, all: Message[]) =>
          all.findIndex((candidate) => candidate.id === message.id) === index
      ) as Message[]

      store.cacheMessages(conversationId, unique)
      store.setMessages(unique)
    } catch (error) {
      if (isAbortError(error)) return
      console.error('[ChatService] loadMessages failed:', error)
    } finally {
      loadAbortController = null
      store.setLoadingMessages(false)
    }
  },

  /**
   * 发送消息。响应头建立前失败会安全重试；收到流之后再失败则续传 run。
   */
  async sendMessage(
    content: string,
    conversationId?: string,
    options: {
      createUserMessage?: boolean
      attachments?: FileAttachment[]
      enableImageGeneration?: boolean
      imageConfig?: {
        prompt: string
        negative_prompt?: string
        image_size: string
      }
    } = {}
  ): Promise<void> {
    const {
      createUserMessage = true,
      attachments,
      enableImageGeneration,
      imageConfig,
    } = options
    const store = useChatStore.getState()

    if (store.isSendingMessage) {
      console.log('[ChatService] Already sending, skipping')
      return
    }

    store.setSendingMessage(true)

    const userMessageId = createUserMessage ? nanoid() : undefined
    const aiMessageId = nanoid()
    const runId = createRunId()
    const idempotencyKey = createIdempotencyKey(runId)
    const controller = new AbortController()
    const cursor = createCursor(runId)
    let responseAccepted = false

    if (createUserMessage && userMessageId) {
      store.addMessage({
        id: userMessageId,
        role: 'user',
        content,
        attachments,
      })
    }

    store.addMessage({
      id: aiMessageId,
      role: 'assistant',
      content: '',
      thinking: '',
      displayState: 'waiting',
      runId,
      lastSeq: 0,
    })

    activeStreamRequest = {
      controller,
      messageId: aiMessageId,
      runId,
      userAborted: false,
    }

    try {
      const apiMessages = useChatStore
        .getState()
        .messages.filter(
          (message) => message.role !== 'assistant' || message.content.trim()
        )
        .map((message) => ({
          role: message.role,
          content: message.content,
        }))

      const response = await requestChatResponse(
        {
          messages: apiMessages,
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
          runId,
        },
        idempotencyKey,
        controller.signal
      )

      responseAccepted = true
      cursor.runId = response.headers.get('X-Run-ID') || runId
      activeStreamRequest.runId = cursor.runId

      const newTitle = response.headers.get('X-Conversation-Title')
      if (newTitle && conversationId) {
        const decodedTitle = decodeURIComponent(newTitle)
        try {
          const { useConversationStore } =
            await import('@/features/conversation/store/conversation-store')
          useConversationStore.setState((state) => ({
            conversations: state.conversations.map((conversation) =>
              conversation.id === conversationId
                ? { ...conversation, title: decodedTitle }
                : conversation
            ),
          }))
        } catch {
          // Sidebar title sync is best effort.
        }
      }

      const reader = response.body?.getReader()
      if (!reader) {
        throw new ChatRequestError('响应没有可读取的流', false)
      }

      await this.handleStream(reader, aiMessageId, cursor)

      if (!cursor.completed && !cursor.terminal) {
        const resumed = await this.resumeRun(
          cursor,
          aiMessageId,
          controller.signal
        )
        if (!resumed) {
          this.markInterruptedOrError(aiMessageId, cursor)
        }
      }
    } catch (error) {
      const active = activeStreamRequest

      if (
        active?.userAborted ||
        controller.signal.aborted ||
        isAbortError(error)
      ) {
        store.transitionPhase(aiMessageId, { type: 'COMPLETE' })
        store.updateMessage(aiMessageId, {
          displayState: 'idle',
          hasError: false,
          runId: cursor.runId || runId,
          lastSeq: cursor.lastSeq,
        })
        store.stopStreaming('user_stop')
        return
      }

      if (responseAccepted && cursor.runId) {
        const resumed = await this.resumeRun(
          cursor,
          aiMessageId,
          controller.signal
        )
        if (resumed) return
      }

      console.error('[ChatService] sendMessage failed:', error)
      this.markInterruptedOrError(aiMessageId, cursor, responseAccepted)
    } finally {
      if (activeStreamRequest?.controller === controller) {
        activeStreamRequest = null
      }

      store.setSendingMessage(false)
      store.stopStreaming()

      if (conversationId) {
        const currentMessages = useChatStore.getState().messages
        useChatStore.getState().cacheMessages(conversationId, currentMessages)
      }
    }
  },

  /**
   * 处理一段 SSE 流。事件按 seq 去重，失败时由调用方决定是否续传。
   */
  async handleStream(
    reader: ReadableStreamDefaultReader<Uint8Array>,
    messageId: string,
    cursor: StreamCursor
  ): Promise<void> {
    let store = useChatStore.getState()
    if (!store.messageStates.has(messageId)) {
      store.initMessageState(messageId)
      store = useChatStore.getState()
    }

    const thinkingBuffer = new StreamBuffer({
      onFlush: (content) =>
        useChatStore.getState().appendThinking(messageId, content),
    })

    const answerBuffer = new StreamBuffer({
      onFlush: (content) =>
        useChatStore.getState().appendContent(messageId, content),
    })

    try {
      await SSEParser.parseStream(reader, {
        onData: (data, eventId) => {
          const seq = extractSeq(data, eventId)
          if (seq !== null) {
            if (seq <= cursor.lastSeq) return
            cursor.lastSeq = seq
          }

          cursor.receivedEvent = true
          cursor.runId = data.runId || cursor.runId
          const currentStore = useChatStore.getState()

          if (data.type === 'thinking' && data.content) {
            if (currentStore.streamingPhase !== 'thinking') {
              currentStore.startStreaming(messageId, 'thinking')
              currentStore.transitionPhase(messageId, {
                type: 'START_THINKING',
              })
              currentStore.updateMessage(messageId, {
                displayState: 'streaming',
                runId: cursor.runId || undefined,
                lastSeq: cursor.lastSeq,
              })
            }

            if (data.step) {
              thinkingBuffer.append(`${data.content}\n`)
              thinkingBuffer.forceFlush()
            } else {
              thinkingBuffer.append(data.content)
            }
          } else if (data.type === 'answer' && data.content) {
            if (currentStore.streamingPhase !== 'answer') {
              currentStore.startStreaming(messageId, 'answer')
              currentStore.transitionPhase(messageId, {
                type: 'START_ANSWERING',
              })
              currentStore.updateMessage(messageId, {
                displayState: 'streaming',
                runId: cursor.runId || undefined,
                lastSeq: cursor.lastSeq,
              })
            }
            answerBuffer.append(data.content)
          } else if (data.type === 'tool_call') {
            const toolCallId = data.toolCallId || nanoid()
            currentStore.transitionPhase(messageId, {
              type: 'START_TOOL_CALL',
              toolCallId,
              name: data.name || 'unknown',
              args: data.args,
            })

            const message = currentStore.messages.find(
              (item) => item.id === messageId
            )
            const invocations = message?.toolInvocations || []
            currentStore.updateMessage(messageId, {
              toolInvocations: [
                ...invocations,
                {
                  toolCallId,
                  name: data.name || 'unknown',
                  state: 'running',
                  args: data.args,
                },
              ],
              displayState: 'streaming',
              runId: cursor.runId || undefined,
              lastSeq: cursor.lastSeq,
            })
          } else if (data.type === 'tool_progress') {
            if (data.toolCallId && data.progress !== undefined) {
              currentStore.transitionPhase(messageId, {
                type: 'TOOL_PROGRESS',
                toolCallId: data.toolCallId,
                progress: data.progress,
                estimatedTime: data.estimatedTime,
              })
              currentStore.updateToolProgress(
                messageId,
                data.toolCallId,
                data.progress,
                data.estimatedTime
              )
            }
          } else if (data.type === 'tool_result') {
            const toolCallId = data.toolCallId || ''
            currentStore.transitionPhase(messageId, {
              type: 'TOOL_COMPLETE',
              toolCallId,
              success: data.success ?? false,
              result: data,
            })

            const message = currentStore.messages.find(
              (item) => item.id === messageId
            )
            const invocations = message?.toolInvocations || []
            currentStore.updateMessage(messageId, {
              toolInvocations: invocations.map((invocation) =>
                invocation.toolCallId === toolCallId
                  ? applyToolResult(invocation, data)
                  : invocation
              ),
              runId: cursor.runId || undefined,
              lastSeq: cursor.lastSeq,
            })
          } else if (data.type === 'error') {
            thinkingBuffer.forceFlush()
            answerBuffer.forceFlush()
            cursor.terminal = true
            cursor.cancelled = Boolean(data.cancelled)

            if (data.cancelled) {
              currentStore.transitionPhase(messageId, { type: 'COMPLETE' })
              currentStore.updateMessage(messageId, {
                displayState: 'idle',
                hasError: false,
                runId: cursor.runId || undefined,
                lastSeq: cursor.lastSeq,
              })
              currentStore.stopStreaming('user_stop')
            } else {
              currentStore.transitionPhase(messageId, {
                type: 'ERROR',
                message: data.message || '生成失败',
              })
              currentStore.updateMessage(messageId, {
                displayState: 'error',
                hasError: true,
                runId: cursor.runId || undefined,
                lastSeq: cursor.lastSeq,
              })
              currentStore.stopStreaming('network_error')
            }
          } else if (data.type === 'complete') {
            thinkingBuffer.forceFlush()
            answerBuffer.forceFlush()
            this.markCompleted(messageId, cursor)
          }
        },
      })
    } finally {
      thinkingBuffer.forceFlush()
      answerBuffer.forceFlush()
      thinkingBuffer.destroy()
      answerBuffer.destroy()
    }
  },

  /**
   * 从同一个 run 的 lastSeq 之后继续接收事件。
   */
  async resumeRun(
    cursor: StreamCursor,
    messageId: string,
    signal: AbortSignal
  ): Promise<boolean> {
    if (!cursor.runId || cursor.completed) return cursor.completed
    if (cursor.terminal) return false

    let lastError: ChatRequestError | null = null

    for (let attempt = 1; attempt <= MAX_CONNECT_ATTEMPTS; attempt += 1) {
      try {
        const response = await fetchWithTimeout(
          `/v1/chat/runs/${encodeURIComponent(cursor.runId)}`,
          {
            method: 'GET',
            headers: {
              'Last-Event-ID': String(cursor.lastSeq),
            },
          },
          signal
        )

        if (
          response.status === 204 ||
          response.status === 404 ||
          response.status === 410
        ) {
          return false
        }

        if (!response.ok) {
          const error = await toHttpError(response)
          if (!error.retryable || attempt === MAX_CONNECT_ATTEMPTS) {
            if (!error.retryable) return false
            lastError = error
          } else {
            lastError = error
          }
        } else {
          const runStatus = response.headers.get('X-Run-Status')
          const reader = response.body?.getReader()

          if (!reader) return false

          await this.handleStream(reader, messageId, cursor)

          if (cursor.completed) return true
          if (cursor.terminal) return false

          if (runStatus === 'completed') {
            this.markCompleted(messageId, cursor)
            return true
          }
        }
      } catch (error) {
        if (signal.aborted) throw createAbortError()

        const requestError =
          error instanceof ChatRequestError
            ? error
            : new ChatRequestError(
                error instanceof Error ? error.message : '续传失败',
                true
              )

        if (!requestError.retryable || attempt === MAX_CONNECT_ATTEMPTS) {
          lastError = requestError
        } else {
          lastError = requestError
        }
      }

      await wait(getRetryDelay(attempt), signal)
    }

    if (lastError) {
      console.warn('[ChatService] resume failed:', lastError.message)
    }
    return false
  },

  /**
   * 用户点击“继续生成”，从消息保存的 lastSeq 恢复。
   */
  async resumeMessage(
    conversationId: string,
    messageId: string
  ): Promise<void> {
    const store = useChatStore.getState()
    const message = store.messages.find((item) => item.id === messageId)

    if (!message?.runId || store.isSendingMessage) return

    const controller = new AbortController()
    const cursor = createCursor(message.runId, message.lastSeq || 0)
    cursor.receivedEvent = Boolean(message.content || message.thinking)

    store.setSendingMessage(true)
    activeStreamRequest = {
      controller,
      messageId,
      runId: message.runId,
      userAborted: false,
    }

    try {
      const resumed = await this.resumeRun(cursor, messageId, controller.signal)
      if (!resumed) {
        this.markInterruptedOrError(messageId, cursor, true)
      }
    } catch (error) {
      if (!isAbortError(error)) {
        console.error('[ChatService] resumeMessage failed:', error)
        this.markInterruptedOrError(messageId, cursor, true)
      }
    } finally {
      if (activeStreamRequest?.controller === controller) {
        activeStreamRequest = null
      }
      store.setSendingMessage(false)
      store.stopStreaming()
      store.cacheMessages(conversationId, useChatStore.getState().messages)
    }
  },

  markCompleted(messageId: string, cursor: StreamCursor): void {
    cursor.completed = true
    cursor.terminal = true

    const store = useChatStore.getState()
    store.transitionPhase(messageId, { type: 'COMPLETE' })
    store.updateMessage(messageId, {
      displayState: 'idle',
      hasError: false,
      runId: cursor.runId || undefined,
      lastSeq: cursor.lastSeq,
    })
    store.stopStreaming()
  },

  markInterruptedOrError(
    messageId: string,
    cursor: StreamCursor,
    responseAccepted = false
  ): void {
    const store = useChatStore.getState()
    const message = store.messages.find((item) => item.id === messageId)
    const hasPartialContent = Boolean(message?.content || message?.thinking)
    const interrupted =
      responseAccepted && (cursor.receivedEvent || hasPartialContent)

    if (interrupted) {
      store.transitionPhase(messageId, { type: 'COMPLETE' })
    } else {
      store.transitionPhase(messageId, {
        type: 'ERROR',
        message: '连接已中断',
      })
    }
    store.updateMessage(messageId, {
      displayState: interrupted ? 'interrupted' : 'error',
      hasError: true,
      runId: cursor.runId || undefined,
      lastSeq: cursor.lastSeq,
    })
    store.stopStreaming(interrupted ? 'network_error' : undefined)
  },

  /**
   * 重新生成上一条回答。
   */
  async retryMessage(conversationId: string, messageId: string): Promise<void> {
    const store = useChatStore.getState()

    if (store.streamingMessageId) {
      store.stopStreaming('user_retry')
    }

    const index = store.messages.findIndex(
      (message) => message.id === messageId
    )
    if (index === -1) return

    const message = store.messages[index]
    if (message.role !== 'assistant') return

    const removed = store.removeMessagesFrom(index)
    const idsToDelete = removed.map((item) => item.id)

    const lastUserMessage = [...store.messages]
      .reverse()
      .find((item) => item.role === 'user')
    if (!lastUserMessage) return

    if (idsToDelete.length > 0) {
      apiFetch('/v1/messages/delete', {
        method: 'POST',
        body: JSON.stringify({ messageIds: idsToDelete }),
      }).catch(console.error)
    }

    await this.sendMessage(lastUserMessage.content, conversationId, {
      createUserMessage: false,
      attachments: lastUserMessage.attachments,
    })
  },

  /**
   * 编辑并重发。
   */
  async editAndResend(
    conversationId: string,
    messageId: string,
    newContent: string
  ): Promise<void> {
    const store = useChatStore.getState()
    const index = store.messages.findIndex(
      (message) => message.id === messageId
    )

    if (index === -1) return
    if (store.messages[index].role !== 'user') return

    const removed = store.removeMessagesFrom(index)
    const idsToDelete = removed.map((item) => item.id)

    if (idsToDelete.length > 0) {
      apiFetch('/v1/messages/delete', {
        method: 'POST',
        body: JSON.stringify({ messageIds: idsToDelete }),
      }).catch(console.error)
    }

    await this.sendMessage(newContent, conversationId, {
      createUserMessage: true,
    })
  },
}
