// src/pages/AdminUsersPage.tsx
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ShieldX, UsersRound } from 'lucide-react'
import { toast } from 'sonner'
import { MainLayout } from '@/components/MainLayout'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { AdminToolbar } from '@/features/admin/components/AdminToolbar'
import { CreateMemberDialog } from '@/features/admin/components/CreateMemberDialog'
import { InviteMemberDialog } from '@/features/admin/components/InviteMemberDialog'
import { MemberTable } from '@/features/admin/components/MemberTable'
import { PendingInvitationsList } from '@/features/admin/components/PendingInvitationsList'
import { RolesEditor } from '@/features/admin/components/RolesEditor'
import type {
  ActionResult,
  InviteActionResult,
} from '@/features/admin/store/admin.store'
import { useAdminStore } from '@/features/admin/store/admin.store'
import type {
  CreateInvitationRequest,
  CreateUserRequest,
} from '@/features/admin/services/admin.service'
import {
  hasAdminRole,
  isPendingInvitation,
  type AdminTab,
  type UserSummary,
} from '@/features/admin/types'
import { useAuthStore } from '@/features/auth/store/auth.store'

const TABS: { value: AdminTab; label: string }[] = [
  { value: 'members', label: '成员' },
  { value: 'invitations', label: '邀请' },
]

export function AdminUsersPage() {
  const user = useAuthStore((state) => state.user)
  const isAdmin = hasAdminRole(user?.roles)

  const members = useAdminStore((state) => state.members)
  const total = useAdminStore((state) => state.total)
  const page = useAdminStore((state) => state.page)
  const size = useAdminStore((state) => state.size)
  const query = useAdminStore((state) => state.query)
  const statusFilter = useAdminStore((state) => state.statusFilter)
  const membersLoading = useAdminStore((state) => state.membersLoading)
  const membersError = useAdminStore((state) => state.membersError)

  const roles = useAdminStore((state) => state.roles)
  const invitations = useAdminStore((state) => state.invitations)
  const invitationsLoading = useAdminStore((state) => state.invitationsLoading)
  const invitationsError = useAdminStore((state) => state.invitationsError)

  const creatingUser = useAdminStore((state) => state.creatingUser)
  const creatingInvitation = useAdminStore((state) => state.creatingInvitation)
  const updatingRoles = useAdminStore((state) => state.updatingRoles)
  const togglingEnabled = useAdminStore((state) => state.togglingEnabled)
  const revokingInvitations = useAdminStore((state) => state.revokingInvitations)

  const load = useAdminStore((state) => state.load)
  const loadRoles = useAdminStore((state) => state.loadRoles)
  const loadInvitations = useAdminStore((state) => state.loadInvitations)
  const setQuery = useAdminStore((state) => state.setQuery)
  const setStatusFilter = useAdminStore((state) => state.setStatusFilter)
  const setPage = useAdminStore((state) => state.setPage)
  const createUser = useAdminStore((state) => state.createUser)
  const changeRoles = useAdminStore((state) => state.changeRoles)
  const setEnabled = useAdminStore((state) => state.setEnabled)
  const invite = useAdminStore((state) => state.invite)
  const revokeInvitation = useAdminStore((state) => state.revokeInvitation)

  const [tab, setTab] = useState<AdminTab>('members')
  const [createOpen, setCreateOpen] = useState(false)
  const [inviteOpen, setInviteOpen] = useState(false)
  const [invitePreset, setInvitePreset] = useState<{
    email: string
    name?: string | null
    roles?: string[] | null
  } | null>(null)
  const [rolesTarget, setRolesTarget] = useState<UserSummary | null>(null)

  useEffect(() => {
    if (!isAdmin) return
    void load()
    void loadRoles()
    void loadInvitations()
  }, [isAdmin, load, loadRoles, loadInvitations])

  const pendingInvitations = invitations.filter(isPendingInvitation).length

  async function handleCreate(payload: CreateUserRequest): Promise<ActionResult> {
    const result = await createUser(payload)
    if (result.ok) {
      toast.success(result.message)
    } else {
      toast.error(result.message)
    }
    return result
  }

  async function handleInvite(payload: CreateInvitationRequest): Promise<InviteActionResult> {
    const result = await invite(payload)
    // 成功链接由对话框负责展示，这里只报失败，避免重复提示。
    if (!result.ok) {
      toast.error(result.message)
    }
    return result
  }

  async function handleChangeRoles(userId: string, nextRoles: string[]): Promise<ActionResult> {
    const result = await changeRoles(userId, nextRoles)
    if (result.ok) {
      toast.success(result.message)
    } else {
      toast.error(result.message)
    }
    return result
  }

  async function handleToggleEnabled(member: UserSummary, enabled: boolean) {
    if (!enabled) {
      const confirmed = window.confirm(
        `确定停用 ${member.email} 吗？停用后该账号将无法登录。`
      )
      if (!confirmed) return
    }
    const result = await setEnabled(member.id, enabled)
    if (result.ok) {
      toast.success(result.message)
    } else {
      toast.error(result.message)
    }
  }

  async function handleRevoke(invitationId: string) {
    const result = await revokeInvitation(invitationId)
    if (result.ok) {
      toast.success(result.message)
    } else {
      toast.error(result.message)
    }
  }

  function openInviteForMember(member: UserSummary) {
    setInvitePreset({
      email: member.email,
      name: member.name,
      roles: member.roles,
    })
    setInviteOpen(true)
  }

  function openBlankInvite() {
    setInvitePreset(null)
    setInviteOpen(true)
  }

  // 无权限时不渲染空表格，直接说明原因——空表格会让人以为是数据问题。
  if (!isAdmin) {
    return (
      <MainLayout>
        <div className="flex h-full items-center justify-center px-6">
          <div className="w-full max-w-md rounded-xl border border-gray-200 bg-white p-6 text-center dark:border-gray-800 dark:bg-gray-900">
            <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-red-50 text-red-600 dark:bg-red-950/40 dark:text-red-400">
              <ShieldX className="h-5 w-5" />
            </div>
            <h1 className="mt-3 text-base font-semibold text-gray-900 dark:text-gray-100">
              无权限访问
            </h1>
            <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
              成员管理仅对管理员与用户管理员开放，当前账号缺少 admin / user_admin 角色。
            </p>
            <p className="mt-2 text-xs text-gray-400 dark:text-gray-500">
              如果你认为这是误判，请让管理员在成员管理中为你分配对应角色。
            </p>
            <Button asChild variant="outline" className="mt-4">
              <Link to="/chat">返回对话</Link>
            </Button>
          </div>
        </div>
      </MainLayout>
    )
  }

  return (
    <MainLayout>
      <div className="flex h-full flex-col">
        <header className="border-b border-gray-200 px-6 py-4 dark:border-gray-800">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-50 text-blue-600 dark:bg-blue-950/40 dark:text-blue-400">
                <UsersRound className="h-4.5 w-4.5" />
              </div>
              <div>
                <h1 className="text-base font-semibold text-gray-900 dark:text-gray-100">
                  成员管理
                </h1>
                <p className="text-xs text-gray-500 dark:text-gray-400">
                  账号由管理员开通，受邀人通过邀请链接自行设置密码
                </p>
              </div>
            </div>

            <div className="inline-flex rounded-lg bg-gray-100 p-0.5 dark:bg-gray-900">
              {TABS.map((item) => {
                const active = tab === item.value
                return (
                  <button
                    key={item.value}
                    type="button"
                    onClick={() => setTab(item.value)}
                    className={`relative rounded-md px-3 py-1.5 text-sm transition-colors ${
                      active
                        ? 'bg-white text-gray-900 shadow-sm dark:bg-gray-800 dark:text-gray-100'
                        : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'
                    }`}
                  >
                    {item.label}
                    {item.value === 'invitations' && pendingInvitations > 0 && (
                      <span className="ml-1.5 rounded-full bg-amber-500 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                        {pendingInvitations}
                      </span>
                    )}
                  </button>
                )
              })}
            </div>
          </div>
        </header>

        <ScrollArea className="flex-1 min-h-0">
          <div className="px-6 py-4">
            {tab === 'members' ? (
              <div className="space-y-4">
                <AdminToolbar
                  query={query}
                  statusFilter={statusFilter}
                  page={page}
                  size={size}
                  total={total}
                  loading={membersLoading}
                  onQueryChange={setQuery}
                  onStatusFilterChange={setStatusFilter}
                  onPageChange={setPage}
                  onRefresh={() => void load()}
                  onCreateMember={() => setCreateOpen(true)}
                  onInviteMember={openBlankInvite}
                />

                {membersError && !membersLoading && (
                  <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-400">
                    {membersError}
                  </div>
                )}

                <MemberTable
                  members={members}
                  loading={membersLoading}
                  currentUserId={user?.id}
                  updatingRoles={updatingRoles}
                  togglingEnabled={togglingEnabled}
                  onChangeRoles={setRolesTarget}
                  onToggleEnabled={(member, enabled) => void handleToggleEnabled(member, enabled)}
                  onInvite={openInviteForMember}
                />
              </div>
            ) : (
              <PendingInvitationsList
                invitations={invitations}
                loading={invitationsLoading}
                error={invitationsError}
                revokingIds={revokingInvitations}
                onRevoke={(invitation) => void handleRevoke(invitation.id)}
                onRefresh={() => void loadInvitations()}
                onInvite={openBlankInvite}
              />
            )}
          </div>
        </ScrollArea>
      </div>

      <CreateMemberDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        roles={roles}
        submitting={creatingUser}
        onSubmit={handleCreate}
      />

      <InviteMemberDialog
        open={inviteOpen}
        onOpenChange={(open) => {
          setInviteOpen(open)
          if (!open) setInvitePreset(null)
        }}
        roles={roles}
        submitting={creatingInvitation}
        preset={invitePreset}
        onSubmit={handleInvite}
      />

      <RolesEditor
        member={rolesTarget}
        open={rolesTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRolesTarget(null)
        }}
        roles={roles}
        saving={Boolean(rolesTarget && updatingRoles[rolesTarget.id])}
        currentUserId={user?.id}
        onSubmit={handleChangeRoles}
      />
    </MainLayout>
  )
}
