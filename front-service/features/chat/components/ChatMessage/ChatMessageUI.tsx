/**
 * Chat Message UI Component - 消息 UI 组件
 * 
 * 纯展示组件，负责渲染单条消息
 * 根据 role 区分用户消息和 AI 消息的渲染方式
 * 
 * @module modules/chat-message/ChatMessageUI
 */

import { useState } from 'react'
import { ThinkingPanel } from '@/features/chat/components/ThinkingPanel'
import { MessageContent } from '@/features/chat/components/MessageContent'
import { MessageActions } from '@/features/chat/components/MessageActions'
import { Button } from '@/components/ui/button'
import { Loader2, Edit2, RotateCw, ChevronDown, ChevronRight, Globe, XCircle, HelpCircle } from 'lucide-react'
import { MarkdownIcon } from '@/components/icons/MarkdownIcon'
import { TextFileIcon } from '@/components/icons/TextFileIcon'
import { cn } from '@/lib/utils'
import type { Message, ToolInvocation, ToolResult, SearchSource, Clarification } from '@/features/chat/types/chat'
import type { MessagePhase } from '@/features/chat/types/message-state'

/**
 * 搜索状态组件 - 简洁风格，类似 Perplexity
 */
function WebSearchStatus({ invocation }: { invocation: ToolInvocation }) {
  const [isExpanded, setIsExpanded] = useState(false)
  const sources = invocation.result?.sources as SearchSource[] | undefined

  if (invocation.state === 'running' || invocation.state === 'pending') {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        <span>搜索中...</span>
        {invocation.args?.query && (
          <span className="text-xs opacity-70">&quot;{invocation.args.query}&quot;</span>
        )}
      </div>
    )
  }

  if (invocation.state === 'failed') {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <XCircle className="h-3.5 w-3.5" />
        <span>搜索失败</span>
      </div>
    )
  }

  const hasSources = sources && sources.length > 0
  
  return (
    <div className="space-y-2">
      {hasSources && (
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-muted-foreground">来源:</span>
          {sources.slice(0, isExpanded ? sources.length : 3).map((source, index) => (
            <a
              key={index}
              href={source.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md bg-muted/50 hover:bg-muted text-xs transition-colors group"
            >
              <Globe className="h-3 w-3 text-muted-foreground" />
              <span className="text-foreground/80 group-hover:text-foreground max-w-[120px] truncate">
                {new URL(source.url).hostname.replace('www.', '')}
              </span>
            </a>
          ))}
          {sources.length > 3 && (
            <button
              onClick={() => setIsExpanded(!isExpanded)}
              className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
            >
              {isExpanded ? (
                <>收起 <ChevronDown className="h-3 w-3" /></>
              ) : (
                <>+{sources.length - 3} 更多 <ChevronRight className="h-3 w-3" /></>
              )}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * 渲染单个工具调用
 */
function ToolInvocationItem({ 
  invocation, 
  onCancel 
}: { 
  invocation: ToolInvocation
  onCancel?: (toolCallId: string) => void
}) {
  if (invocation.name === 'generate_image') {
    if (invocation.state === 'running' || invocation.state === 'pending') {
      const progress = (invocation as { progress?: number }).progress ?? 0
      const estimatedTime = (invocation as { estimatedTime?: number }).estimatedTime
      
      return (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              <span>正在生成图片...</span>
              {progress > 0 && <span className="text-xs">{progress}%</span>}
              {estimatedTime && estimatedTime > 0 && (
                <span className="text-xs opacity-70">约 {estimatedTime}s</span>
              )}
            </div>
            {onCancel && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onCancel(invocation.toolCallId)}
                className="h-6 px-2 text-xs text-muted-foreground hover:text-destructive"
              >
                取消
              </Button>
            )}
          </div>
          {progress > 0 && (
            <div className="h-1 w-full bg-muted rounded-full overflow-hidden">
              <div 
                className="h-full bg-primary transition-all duration-300"
                style={{ width: `${progress}%` }}
              />
            </div>
          )}
        </div>
      )
    }
    if (invocation.state === 'failed') {
      return (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <XCircle className="h-3.5 w-3.5" />
          <span>图片生成失败</span>
        </div>
      )
    }
    if (invocation.state === 'cancelled') {
      return (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <XCircle className="h-3.5 w-3.5" />
          <span>已取消</span>
        </div>
      )
    }
    return null
  }

  if (invocation.name === 'web_search') {
    return <WebSearchStatus invocation={invocation} />
  }

  return null
}

function ToolResultItem({ result }: { result: ToolResult }) {
  const [isExpanded, setIsExpanded] = useState(false)

  if (result.name === 'generate_image') return null

  if (result.name === 'web_search' && result.result.sources && result.result.sources.length > 0) {
    const sources = result.result.sources as SearchSource[]
    return (
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-muted-foreground">来源:</span>
        {sources.slice(0, isExpanded ? sources.length : 3).map((source, index) => (
          <a
            key={index}
            href={source.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md bg-muted/50 hover:bg-muted text-xs transition-colors group"
          >
            <Globe className="h-3 w-3 text-muted-foreground" />
            <span className="text-foreground/80 group-hover:text-foreground max-w-[120px] truncate">
              {new URL(source.url).hostname.replace('www.', '')}
            </span>
          </a>
        ))}
        {sources.length > 3 && (
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
          >
            {isExpanded ? (
              <>收起 <ChevronDown className="h-3 w-3" /></>
            ) : (
              <>+{sources.length - 3} 更多 <ChevronRight className="h-3 w-3" /></>
            )}
          </button>
        )}
      </div>
    )
  }
  return null
}

/**
 * 工具名/字段名 → 中文标签。
 *
 * 服务端的 `missingFields` 在无法更精确时会给出工具名（例如 `ticket_get`）。把工具名直接
 * 摆给用户（"需要：ticket_get"）等于没解释；未知字段原样显示，因为悄悄丢掉服务端给出的
 * 线索比显示一个英文标识更糟。
 */
const CLARIFICATION_FIELD_LABELS: Record<string, string> = {
  ticket_get: '工单编号',
  ticket_list: '查询条件',
  ticket_create: '工单内容',
  ticket_comment: '回复内容',
  ticket_merge: '待合并的两个工单编号',
  number: '工单编号',
  ticketId: '工单编号',
  title: '标题',
  body: '回复内容',
}

function fieldLabel(field: string): string {
  return CLARIFICATION_FIELD_LABELS[field] || field
}

/**
 * 槽位回填卡片：工具确定性失败后，服务端要求用户补充信息。
 *
 * 这张卡片是"提问"而不是"报错"——它不改 displayState，也不阻止用户继续输入；
 * 用户的下一条消息就是回填。
 */
function ClarificationCard({
  clarification,
  onFocusInput,
}: {
  clarification: Clarification
  onFocusInput?: () => void
}) {
  return (
    <div className="space-y-2 rounded-2xl border border-amber-300/70 bg-amber-50/60 p-3.5 dark:border-amber-800/70 dark:bg-amber-950/20">
      <div className="flex items-center gap-2 text-sm font-medium text-amber-800 dark:text-amber-300">
        <HelpCircle className="h-4 w-4 shrink-0" />
        <span>需要你补充信息后继续</span>
      </div>
      <p className="text-sm leading-relaxed whitespace-pre-wrap text-[hsl(var(--text-primary))]">
        {clarification.question}
      </p>
      {clarification.missingFields.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-[hsl(var(--text-secondary))]">需要：</span>
          {clarification.missingFields.map((field) => (
            <span
              key={field}
              className="rounded-md bg-amber-100/80 px-2 py-0.5 text-xs text-amber-900 dark:bg-amber-900/30 dark:text-amber-200"
            >
              {fieldLabel(field)}
            </span>
          ))}
        </div>
      )}
      {onFocusInput && (
        <Button
          variant="outline"
          size="sm"
          onClick={onFocusInput}
          className="h-7 px-2.5 text-xs"
        >
          去补充
        </Button>
      )}
    </div>
  )
}

/**
 * 错误说明。
 *
 * 之前这里只有固定的"生成失败"，服务端的说明（"本次生成已有 120s 没有进展，请重试；
 * 若刚才发生过写操作，请先确认结果再重试"）会在 store 里被丢掉 —— 而那句话正是用户
 * 需要照做的部分。
 */
function MessageErrorBlock({ detail }: { detail?: string }) {
  return (
    <div className="flex items-start gap-2 text-red-500">
      <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
      <span className="text-sm leading-relaxed whitespace-pre-wrap">{detail || '生成失败'}</span>
    </div>
  )
}

interface ChatMessageUIProps {
  message: Message
  messageId: string
  phase: MessagePhase
  isProcessing: boolean
  isWaitingForResponse: boolean
  /** 服务端的错误说明；缺省时回退到通用文案。 */
  errorMessage?: string
  onRetry?: () => void
  onResume?: () => void
  onEdit?: (newContent: string) => void
  onCancelTool?: (toolCallId: string) => void
  /** 只在"这是最后一条消息且当前没有在发送"时给：否则会出现一个指向过去的、点了没用的按钮。 */
  onFocusInput?: () => void
}

export function ChatMessageUI({
  message,
  messageId,
  phase,
  isProcessing,
  errorMessage,
  onRetry,
  onResume,
  onEdit,
  onCancelTool,
  onFocusInput,
}: ChatMessageUIProps) {
  const isUser = message.role === 'user'

  const isStreaming = isProcessing
  const isStreamingAnswer = phase === 'answering'
  
  // ============ 用户消息 ============
  if (isUser) {
    return (
      <div className="w-full py-4 group">
        <div className="flex justify-end items-start gap-2">
          {onEdit && (
            <Button
              variant="ghost"
              size="icon"
              onClick={() => onEdit(message.content)}
              className="h-8 w-8 opacity-0 group-hover:opacity-100 transition-opacity mt-2"
            >
              <Edit2 className="h-4 w-4" />
            </Button>
          )}

          <div className="max-w-[70%] flex flex-col items-end gap-2">
            {message.attachments && message.attachments.length > 0 && (
              <div className="flex flex-wrap gap-2 justify-end">
                {message.attachments.map((file, index) => (
                  <div
                    key={index}
                    className={cn(
                      "flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs",
                      file.type === 'md'
                        ? "bg-orange-50 dark:bg-orange-900/20 border border-orange-200 dark:border-orange-800"
                        : "bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800"
                    )}
                  >
                    {file.type === 'md' ? (
                      <MarkdownIcon className="h-3 w-3 text-orange-500" />
                    ) : (
                      <TextFileIcon className="h-3 w-3 text-blue-500" />
                    )}
                    <span className={cn(
                      "font-medium",
                      file.type === 'md'
                        ? "text-orange-700 dark:text-orange-300"
                        : "text-blue-700 dark:text-blue-300"
                    )}>
                      {file.name}
                    </span>
                    <span className={cn(
                      "text-xs",
                      file.type === 'md'
                        ? "text-orange-500 dark:text-orange-400"
                        : "text-blue-500 dark:text-blue-400"
                    )}>
                      {(file.size / 1024).toFixed(1)}KB
                    </span>
                  </div>
                ))}
              </div>
            )}

            <div className="rounded-3xl bg-[hsl(var(--message-user-bg))] px-5 py-3">
              <div className="text-[15px] leading-relaxed whitespace-pre-wrap break-words text-[hsl(var(--text-primary))]">
                {message.content}
              </div>
            </div>
          </div>
        </div>
      </div>
    )
  }
  
  // ============ AI 消息 ============
  const showWaitingIndicator = !isProcessing && !message.thinking && !message.content && message.displayState === 'waiting'
  const showInterruptedIndicator = message.displayState === 'interrupted'
  const showErrorIndicator =
    !showInterruptedIndicator && (phase === 'error' || Boolean(message.hasError))
  // 有内容时错误说明要放在正文之后：正文是模型的输出，说明是"这一轮为什么没跑完"，
  // 顺序反了会让用户先看到一个红条再看到答案。没有内容时它就占据指示器的位置。
  const showErrorAfterContent = showErrorIndicator && Boolean(message.content)

  return (
    <div className="w-full py-6">
      <div className="space-y-4">
        {/* 等待与错误指示器： */}
        {showWaitingIndicator && (
          <div className="flex items-center gap-2 text-[hsl(var(--text-secondary))]">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span className="text-sm">等待响应...</span>
          </div>
        )}

        {showErrorIndicator && !showErrorAfterContent && (
          <MessageErrorBlock detail={errorMessage} />
        )}

        {showInterruptedIndicator && (
          <div className="flex items-center gap-2 text-amber-600 dark:text-amber-500">
            <XCircle className="h-4 w-4" />
            <span className="text-sm">已中断，已保留收到的内容</span>
          </div>
        )}
        {/* 工具调用区： */}
        {message.toolInvocations?.map((invocation) => (
          <ToolInvocationItem 
            key={invocation.toolCallId} 
            invocation={invocation}
            onCancel={onCancelTool}
          />
        ))}

        {!message.toolInvocations?.length && message.toolResults?.map((result) => (
          <ToolResultItem key={result.toolCallId} result={result} />
        ))}
        {/* 思考面板： */}
        {message.thinking && (
          <ThinkingPanel
            messageId={messageId}
            defaultExpanded={true}
          />
        )}
        {/* 回答内容： */}
        {message.content && (
          <div className="prose-container">
            <MessageContent
              content={message.content}
              isStreaming={isStreamingAnswer}
            />
          </div>
        )}
        {showErrorAfterContent && <MessageErrorBlock detail={errorMessage} />}
        {/* 需要补充信息（槽位回填）：用户的下一条消息就是回填，所以这里只提示、不阻塞。 */}
        {message.clarification && (
          <ClarificationCard
            clarification={message.clarification}
            onFocusInput={onFocusInput}
          />
        )}
        {/* 操作按钮区： */}
        {isStreaming && onRetry ? (
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              onClick={onRetry}
              className="h-7 w-7 hover:bg-gray-100 dark:hover:bg-gray-800"
              title="重试"
            >
              <RotateCw className="h-4 w-4" />
            </Button>
          </div>
        ) : message.content || message.displayState === 'interrupted' ? (
          <MessageActions
            content={message.content}
            messageId={message.id}
            role={message.role as 'user' | 'assistant'}
            hasError={message.hasError}
            onRetry={onRetry}
            onResume={onResume}
          />
        ) : null}
      </div>
    </div>
  )
}
