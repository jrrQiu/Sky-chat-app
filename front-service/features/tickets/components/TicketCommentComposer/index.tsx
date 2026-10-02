// features/tickets/components/TicketCommentComposer/index.tsx
import { useEffect, useState, type FormEvent } from 'react'
import { Loader2, Lock, Send } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { TicketActionResult } from '@/features/tickets/store/ticket.store'
import {
  COMMENT_VISIBILITY_LABELS,
  type AddTicketCommentRequest,
  type CommentVisibility,
} from '@/features/tickets/types'

interface TicketCommentComposerProps {
  /** 只有工单处理人（meta.agentRoles）可以写内部备注。 */
  canUseInternal: boolean
  submitting: boolean
  disabled?: boolean
  onSubmit: (payload: AddTicketCommentRequest) => Promise<TicketActionResult>
}

const TEXTAREA_CLASS =
  'min-h-[88px] w-full resize-y rounded-lg border border-input bg-transparent px-2.5 py-2 text-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50 dark:bg-input/30'

/**
 * 回复输入框。
 *
 * 可见性只有两档，且内部备注入口对非处理人完全不渲染——服务端会拒绝，
 * 界面也不应该先给出一个必然失败的按钮。
 */
export function TicketCommentComposer({
  canUseInternal,
  submitting,
  disabled = false,
  onSubmit,
}: TicketCommentComposerProps) {
  const [body, setBody] = useState('')
  const [visibility, setVisibility] = useState<CommentVisibility>('public')
  const [error, setError] = useState('')

  // 权限变化（meta 后到）时把非法选项收回来，避免停在内部备注上。
  useEffect(() => {
    if (!canUseInternal && visibility === 'internal') {
      setVisibility('public')
    }
  }, [canUseInternal, visibility])

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')

    const trimmed = body.trim()
    if (trimmed.length === 0) {
      setError('请输入回复内容')
      return
    }

    const result = await onSubmit({ body: trimmed, visibility })
    if (!result.ok) {
      setError(result.message)
      return
    }
    setBody('')
    setVisibility('public')
  }

  const options: CommentVisibility[] = canUseInternal ? ['public', 'internal'] : ['public']

  return (
    <form onSubmit={handleSubmit} className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="inline-flex rounded-lg bg-gray-100 p-0.5 dark:bg-gray-900">
          {options.map((option) => {
            const active = visibility === option
            return (
              <button
                key={option}
                type="button"
                onClick={() => setVisibility(option)}
                className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs transition-colors ${
                  active
                    ? option === 'internal'
                      ? 'bg-amber-100 text-amber-800 shadow-sm dark:bg-amber-900/50 dark:text-amber-300'
                      : 'bg-white text-gray-900 shadow-sm dark:bg-gray-800 dark:text-gray-100'
                    : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'
                }`}
              >
                {option === 'internal' && <Lock className="h-3 w-3" />}
                {COMMENT_VISIBILITY_LABELS[option]}
              </button>
            )
          })}
        </div>

        <p className="text-xs text-gray-400 dark:text-gray-500">
          回复提交后不可编辑或删除
        </p>
      </div>

      <textarea
        value={body}
        onChange={(event) => setBody(event.target.value)}
        placeholder={
          visibility === 'internal'
            ? '内部备注：仅工单处理人可见，请求人看不到'
            : '公开回复：请求人与处理人都能看到'
        }
        className={`${TEXTAREA_CLASS} ${
          visibility === 'internal'
            ? 'border-amber-300 bg-amber-50/50 dark:border-amber-900 dark:bg-amber-950/20'
            : ''
        }`}
        disabled={submitting || disabled}
        aria-label="工单回复"
      />

      {error && (
        <p className="text-destructive text-sm" role="alert">
          {error}
        </p>
      )}

      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-gray-400 dark:text-gray-500">
          {visibility === 'internal'
            ? '内部备注不会同步给请求人'
            : '公开回复会出现在请求人的工单页'}
        </p>
        <Button type="submit" size="sm" disabled={submitting || disabled}>
          {submitting ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : (
            <Send className="mr-1.5 h-3.5 w-3.5" />
          )}
          发送
        </Button>
      </div>
    </form>
  )
}
