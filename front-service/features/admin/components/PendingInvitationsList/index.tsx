// features/admin/components/PendingInvitationsList/index.tsx
import { Loader2, MailPlus, RefreshCw, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { RoleBadges } from '@/features/admin/components/MemberTable'
import {
  INVITATION_STATUS_LABELS,
  formatDateTime,
  formatRelativeTime,
  isPendingInvitation,
  type InvitationStatus,
  type InvitationSummary,
} from '@/features/admin/types'

interface PendingInvitationsListProps {
  invitations: InvitationSummary[]
  loading: boolean
  error: string | null
  revokingIds: Record<string, boolean>
  onRevoke: (invitation: InvitationSummary) => void
  onRefresh: () => void
  onInvite: () => void
}

const STATUS_STYLES: Record<InvitationStatus, string> = {
  pending:
    'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-400',
  accepted:
    'border-green-200 bg-green-50 text-green-700 dark:border-green-900 dark:bg-green-950/40 dark:text-green-400',
  expired:
    'border-gray-200 bg-gray-50 text-gray-600 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-400',
  revoked:
    'border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-400',
}

export function InvitationStatusBadge({ status }: { status: InvitationStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[status]}`}
    >
      {INVITATION_STATUS_LABELS[status]}
    </span>
  )
}

export function PendingInvitationsList({
  invitations,
  loading,
  error,
  revokingIds,
  onRevoke,
  onRefresh,
  onInvite,
}: PendingInvitationsListProps) {
  const pendingCount = invitations.filter(isPendingInvitation).length

  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-gray-500 dark:text-gray-400">
          共 {invitations.length} 条邀请，其中 {pendingCount} 条待接受
        </p>
        <div className="flex gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={onRefresh}
            disabled={loading}
            className="text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-100"
          >
            <RefreshCw className={`mr-1.5 h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            刷新
          </Button>
          <Button size="sm" onClick={onInvite}>
            <MailPlus className="mr-1.5 h-3.5 w-3.5" />
            发送邀请
          </Button>
        </div>
      </div>

      {loading && invitations.length === 0 && (
        <div className="flex items-center justify-center gap-2 py-16 text-sm text-gray-400">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在加载邀请记录…
        </div>
      )}

      {error && !loading && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-400">
          {error}
        </div>
      )}

      {!loading && !error && invitations.length === 0 && (
        <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
          <MailPlus className="h-8 w-8 text-gray-300 dark:text-gray-600" />
          <p className="text-sm text-gray-500 dark:text-gray-400">暂无邀请记录</p>
          <p className="text-xs text-gray-400 dark:text-gray-500">
            发送邀请后，未接受的邀请会一直显示在这里，可随时撤销
          </p>
        </div>
      )}

      {invitations.map((invitation) => {
        const pending = isPendingInvitation(invitation)
        const revoking = Boolean(revokingIds[invitation.id])
        const expiry = formatRelativeTime(invitation.expiresAt)

        return (
          <div
            key={invitation.id}
            className="flex flex-wrap items-start justify-between gap-3 rounded-xl border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900"
          >
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">
                  {invitation.name?.trim() || invitation.email}
                </span>
                <InvitationStatusBadge status={invitation.status} />
              </div>

              <p className="mt-1 truncate text-xs text-gray-500 dark:text-gray-400">
                {invitation.email}
              </p>

              <div className="mt-1.5">
                <RoleBadges roles={invitation.roles} />
              </div>

              <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-500 dark:text-gray-400">
                {invitation.expiresAt && (
                  <span title={formatDateTime(invitation.expiresAt)}>
                    {pending ? '有效期至' : '过期时间'}
                    {formatDateTime(invitation.expiresAt)}
                    {expiry && `（${expiry}）`}
                  </span>
                )}
                {invitation.invitedBy && <span>邀请人：{invitation.invitedBy}</span>}
                {invitation.acceptedAt && (
                  <span>接受于 {formatDateTime(invitation.acceptedAt)}</span>
                )}
              </div>
            </div>

            {pending && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => onRevoke(invitation)}
                disabled={revoking}
                className="border-red-200 text-red-600 hover:bg-red-50 hover:text-red-700 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950/30"
              >
                {revoking ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                )}
                撤销
              </Button>
            )}
          </div>
        )
      })}
    </div>
  )
}
