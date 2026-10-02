// features/tickets/components/TicketTimeline/index.tsx
import {
  GitMerge,
  History,
  Link2,
  Lock,
  MessageSquare,
  Pencil,
  Plus,
  RefreshCw,
  Split,
  UserPlus,
} from 'lucide-react'
import {
  activityCategory,
  activityLabel,
  formatTicketDateTime,
  humanizeTicketValue,
  isCommentActivityType,
  TICKET_ACTIVITY_DETAIL_LABELS,
  type TicketActivity,
  type TicketActivityCategory,
  type TicketComment,
} from '@/features/tickets/types'

interface TicketTimelineProps {
  /** 已经是"当前用户可见"的评论：非处理人不得包含内部备注。 */
  comments: TicketComment[]
  activities: TicketActivity[]
  loading?: boolean
}

type TimelineEntry =
  | { kind: 'comment'; key: string; at: number | null; comment: TicketComment }
  | { kind: 'activity'; key: string; at: number | null; activity: TicketActivity }

const CATEGORY_ICONS: Record<TicketActivityCategory, typeof History> = {
  status: RefreshCw,
  assignment: UserPlus,
  link: Link2,
  merge: GitMerge,
  split: Split,
  created: Plus,
  updated: Pencil,
  other: History,
}

const CATEGORY_TONES: Record<TicketActivityCategory, string> = {
  status: 'text-blue-500 dark:text-blue-400',
  assignment: 'text-blue-500 dark:text-blue-400',
  link: 'text-gray-400 dark:text-gray-500',
  merge: 'text-gray-400 dark:text-gray-500',
  split: 'text-gray-400 dark:text-gray-500',
  created: 'text-green-600 dark:text-green-400',
  updated: 'text-gray-400 dark:text-gray-500',
  other: 'text-gray-400 dark:text-gray-500',
}

function toTime(value?: string | null): number | null {
  if (!value) return null
  const parsed = new Date(value).getTime()
  return Number.isNaN(parsed) ? null : parsed
}

/** 活动里的 detail 是自由结构，只挑可读的键值展示，最多三条。 */
function detailSummary(detail: Record<string, unknown> | null | undefined): string[] {
  if (!detail) return []
  const parts: string[] = []

  for (const [key, raw] of Object.entries(detail)) {
    if (parts.length >= 3) break
    if (raw === null || raw === undefined || raw === '') continue
    if (typeof raw === 'object') {
      if (Array.isArray(raw) && raw.length === 0) continue
      continue
    }

    const label = TICKET_ACTIVITY_DETAIL_LABELS[key] ?? key
    const value =
      typeof raw === 'string' ? humanizeTicketValue(raw) : String(raw)
    if (!value) continue
    parts.push(`${label}：${value}`)
  }

  return parts
}

/** 状态变更 / 分派 / 关联 / 合并 / 拆分 / 评论合并成一条时间线，按时间正序。 */
export function TicketTimeline({ comments, activities, loading = false }: TicketTimelineProps) {
  const entries: TimelineEntry[] = [
    ...activities
      .filter((activity) => !isCommentActivityType(activity.activityType))
      .map<TimelineEntry>((activity) => ({
        kind: 'activity',
        key: `activity-${activity.id}`,
        at: toTime(activity.createdAt),
        activity,
      })),
    ...comments.map<TimelineEntry>((comment) => ({
      kind: 'comment',
      key: `comment-${comment.id}`,
      at: toTime(comment.createdAt),
      comment,
    })),
  ].sort((a, b) => {
    // 没有时间的条目统一排到最后，而不是被当成 1970 年。
    const left = a.at ?? Number.MAX_SAFE_INTEGER
    const right = b.at ?? Number.MAX_SAFE_INTEGER
    return left - right
  })

  if (loading) {
    return <p className="py-3 text-xs text-gray-400 dark:text-gray-500">正在加载时间线…</p>
  }

  if (entries.length === 0) {
    return (
      <p className="py-3 text-xs text-gray-400 dark:text-gray-500">
        暂无时间线记录：还没有状态变更、分派或回复。
      </p>
    )
  }

  return (
    <ol className="relative space-y-4 border-l border-gray-200 pl-4 dark:border-gray-800">
      {entries.map((entry) => {
        if (entry.kind === 'comment') {
          const { comment } = entry
          const internal = comment.visibility === 'internal'
          return (
            <li key={entry.key} className="relative">
              <span className="absolute -left-[25px] flex h-4 w-4 items-center justify-center rounded-full bg-white dark:bg-gray-950">
                {internal ? (
                  <Lock className="h-3.5 w-3.5 text-amber-500 dark:text-amber-400" />
                ) : (
                  <MessageSquare className="h-3.5 w-3.5 text-gray-400 dark:text-gray-500" />
                )}
              </span>

              <div
                className={`rounded-lg border px-3 py-2 ${
                  internal
                    ? 'border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/30'
                    : 'border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900'
                }`}
              >
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="font-medium text-gray-900 dark:text-gray-100">
                    {comment.authorId}
                  </span>
                  {internal ? (
                    <span className="inline-flex items-center gap-1 rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-medium text-amber-700 dark:bg-amber-900/50 dark:text-amber-300">
                      <Lock className="h-3 w-3" />
                      仅内部可见
                    </span>
                  ) : (
                    <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-500 dark:bg-gray-800 dark:text-gray-400">
                      公开回复
                    </span>
                  )}
                  {comment.authorRoles && (
                    <span className="text-gray-400 dark:text-gray-500">
                      {comment.authorRoles}
                    </span>
                  )}
                  <span className="ml-auto text-gray-400 dark:text-gray-500">
                    {formatTicketDateTime(comment.createdAt)}
                  </span>
                </div>

                <p
                  className={`mt-1.5 text-sm whitespace-pre-wrap ${
                    internal
                      ? 'text-amber-900 dark:text-amber-200'
                      : 'text-gray-700 dark:text-gray-300'
                  }`}
                >
                  {comment.body}
                </p>
              </div>
            </li>
          )
        }

        const { activity } = entry
        const category = activityCategory(activity.activityType)
        const Icon = CATEGORY_ICONS[category]
        const from = humanizeTicketValue(activity.fromValue)
        const to = humanizeTicketValue(activity.toValue)
        const details = detailSummary(activity.detail)

        return (
          <li key={entry.key} className="relative">
            <span className="absolute -left-[25px] flex h-4 w-4 items-center justify-center rounded-full bg-white dark:bg-gray-950">
              <Icon className={`h-3.5 w-3.5 ${CATEGORY_TONES[category]}`} />
            </span>

            <p className="text-sm text-gray-900 dark:text-gray-100">
              <span className="font-medium">{activity.actorId ?? '系统'}</span>{' '}
              <span className="text-gray-600 dark:text-gray-400">
                {activityLabel(activity.activityType)}
              </span>
              {(from || to) && (
                <span className="ml-2 rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-600 dark:bg-gray-800 dark:text-gray-300">
                  {from && to ? `${from} → ${to}` : to ? `→ ${to}` : `← ${from}`}
                </span>
              )}
            </p>

            <p className="mt-0.5 text-xs text-gray-400 dark:text-gray-500">
              {formatTicketDateTime(activity.createdAt)}
            </p>

            {details.length > 0 && (
              <p className="mt-1 space-x-3 text-xs text-gray-500 dark:text-gray-400">
                {details.map((part) => (
                  <span key={part}>{part}</span>
                ))}
              </p>
            )}
          </li>
        )
      })}
    </ol>
  )
}

export function TicketTimelineHeader() {
  return (
    <h4 className="mb-3 flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-gray-100">
      <History className="h-4 w-4 text-gray-400" />
      时间线
    </h4>
  )
}
