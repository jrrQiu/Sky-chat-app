// features/admin/components/MemberTable/index.tsx
import {
  KeyRound,
  Loader2,
  MailPlus,
  MoreHorizontal,
  UserCheck,
  Users,
  UserX,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  MEMBER_STATUS_LABELS,
  formatRelativeTime,
  memberDisplayName,
  roleLabel,
  type MemberStatus,
  type UserSummary,
} from '@/features/admin/types'

interface MemberTableProps {
  members: UserSummary[]
  loading: boolean
  /** 当前登录用户 id，用来标记"我"并提示不要停用自己。 */
  currentUserId?: string
  updatingRoles: Record<string, boolean>
  togglingEnabled: Record<string, boolean>
  onChangeRoles: (member: UserSummary) => void
  onToggleEnabled: (member: UserSummary, enabled: boolean) => void
  onInvite: (member: UserSummary) => void
}

/** 角色用徽章而不是逗号串，多角色时也能一眼扫过。 */
export function RoleBadges({ roles }: { roles?: string[] | null }) {
  const list = roles ?? []
  if (list.length === 0) {
    return <span className="text-xs text-gray-400 dark:text-gray-500">未分配角色</span>
  }

  return (
    <div className="flex flex-wrap gap-1">
      {list.map((role) => {
        const isAdminRole = role === 'admin' || role === 'user_admin'
        return (
          <span
            key={role}
            title={role}
            className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium ${
              isAdminRole
                ? 'border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-400'
                : 'border-gray-200 bg-gray-50 text-gray-600 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-400'
            }`}
          >
            {roleLabel(role)}
          </span>
        )
      })}
    </div>
  )
}

export function MemberStatusBadge({ status }: { status: MemberStatus }) {
  const active = status === 'active'
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${
        active
          ? 'border-green-200 bg-green-50 text-green-700 dark:border-green-900 dark:bg-green-950/40 dark:text-green-400'
          : 'border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-400'
      }`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {MEMBER_STATUS_LABELS[status]}
    </span>
  )
}

export function MemberTable({
  members,
  loading,
  currentUserId,
  updatingRoles,
  togglingEnabled,
  onChangeRoles,
  onToggleEnabled,
  onInvite,
}: MemberTableProps) {
  if (loading && members.length === 0) {
    return (
      <div className="flex items-center justify-center gap-2 py-16 text-sm text-gray-400">
        <Loader2 className="h-4 w-4 animate-spin" />
        正在加载成员列表…
      </div>
    )
  }

  if (members.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-16 text-center">
        <Users className="h-8 w-8 text-gray-300 dark:text-gray-600" />
        <p className="text-sm text-gray-500 dark:text-gray-400">没有匹配的成员</p>
        <p className="text-xs text-gray-400 dark:text-gray-500">
          调整搜索条件，或直接用"新建成员"开通账号
        </p>
      </div>
    )
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-800">
      <table className="w-full min-w-[640px] text-sm">
        <thead className="bg-gray-50 text-xs text-gray-500 dark:bg-gray-900 dark:text-gray-400">
          <tr>
            <th className="px-4 py-2.5 text-left font-medium">成员</th>
            <th className="px-4 py-2.5 text-left font-medium">角色</th>
            <th className="px-4 py-2.5 text-left font-medium">状态</th>
            <th className="px-4 py-2.5 text-left font-medium">创建时间</th>
            <th className="px-4 py-2.5 text-right font-medium">操作</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
          {members.map((member) => {
            const isSelf = Boolean(currentUserId) && member.id === currentUserId
            const isActive = member.status === 'active'
            const rowPending = Boolean(
              updatingRoles[member.id] || togglingEnabled[member.id]
            )

            return (
              <tr key={member.id} className="bg-white dark:bg-gray-950">
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium text-gray-900 dark:text-gray-100">
                      {memberDisplayName(member)}
                    </span>
                    {isSelf && (
                      <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-500 dark:bg-gray-800 dark:text-gray-400">
                        我
                      </span>
                    )}
                  </div>
                  <p className="mt-0.5 truncate text-xs text-gray-500 dark:text-gray-400">
                    {member.email}
                  </p>
                </td>

                <td className="px-4 py-3">
                  <RoleBadges roles={member.roles} />
                </td>

                <td className="px-4 py-3">
                  <MemberStatusBadge status={member.status} />
                  {!isActive && member.disabledAt && (
                    <p className="mt-1 text-[11px] text-gray-400 dark:text-gray-500">
                      {formatRelativeTime(member.disabledAt)}停用
                    </p>
                  )}
                </td>

                <td className="px-4 py-3 text-xs text-gray-500 dark:text-gray-400">
                  {member.createdAt ? formatRelativeTime(member.createdAt) : '—'}
                </td>

                <td className="px-4 py-3 text-right">
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`${member.email} 的操作菜单`}
                        disabled={rowPending}
                      >
                        {rowPending ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <MoreHorizontal className="h-3.5 w-3.5" />
                        )}
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-44">
                      <DropdownMenuLabel>{member.email}</DropdownMenuLabel>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem onSelect={() => onChangeRoles(member)}>
                        <KeyRound />
                        改角色
                      </DropdownMenuItem>
                      <DropdownMenuItem onSelect={() => onInvite(member)}>
                        <MailPlus />
                        发送邀请
                      </DropdownMenuItem>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        variant={isActive ? 'destructive' : 'default'}
                        // 停用自己会立刻把自己踢出管理页，先挡住。
                        disabled={isSelf && isActive}
                        onSelect={() => onToggleEnabled(member, !isActive)}
                      >
                        {isActive ? <UserX /> : <UserCheck />}
                        {isActive ? '停用' : '启用'}
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
