// features/admin/components/RolesEditor/index.tsx
import { useEffect, useState } from 'react'
import { Loader2, ShieldCheck, TriangleAlert } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { RoleMultiSelect } from '@/features/admin/components/RoleMultiSelect'
import type { ActionResult } from '@/features/admin/store/admin.store'
import { memberDisplayName, type UserSummary } from '@/features/admin/types'

interface RolesEditorProps {
  /** 为 null 时对话框关闭；切换成员会重置草稿。 */
  member: UserSummary | null
  open: boolean
  onOpenChange: (open: boolean) => void
  roles: string[]
  saving: boolean
  /** 当前登录用户 id，用于提示"改自己"的风险。 */
  currentUserId?: string
  onSubmit: (memberId: string, roles: string[]) => Promise<ActionResult>
}

export function RolesEditor({
  member,
  open,
  onOpenChange,
  roles,
  saving,
  currentUserId,
  onSubmit,
}: RolesEditorProps) {
  const [selected, setSelected] = useState<string[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open || !member) return
    setSelected(member.roles ?? [])
    setError('')
  }, [open, member])

  const initial = member?.roles ?? []
  const changed =
    selected.length !== initial.length || selected.some((role) => !initial.includes(role))
  const isSelf = Boolean(currentUserId) && member?.id === currentUserId

  const handleSubmit = async () => {
    if (!member) return
    setError('')

    if (selected.length === 0) {
      setError('至少保留一个角色，否则该账号将没有任何权限')
      return
    }

    const result = await onSubmit(member.id, selected)
    if (!result.ok) {
      setError(result.message)
      return
    }
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !saving && onOpenChange(next)}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>修改角色</DialogTitle>
          <DialogDescription>
            {member ? `${memberDisplayName(member)} · ${member.email}` : '选择要分配的角色'}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <RoleMultiSelect
            idPrefix="roles-editor-role"
            roles={roles}
            value={selected}
            onChange={setSelected}
            heldRoles={initial}
            disabled={saving}
          />

          {isSelf && (
            <p className="flex items-center gap-2 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:bg-amber-950/30 dark:text-amber-400">
              <TriangleAlert className="h-3.5 w-3.5 shrink-0" />
              你正在修改自己的角色，移除管理员角色后会立即失去成员管理权限
            </p>
          )}

          {error && (
            <p className="text-destructive text-sm" role="alert">
              {error}
            </p>
          )}
        </div>

        <DialogFooter className="gap-2 sm:justify-between">
          <p className="flex items-center gap-1.5 text-xs text-gray-400 dark:text-gray-500">
            <ShieldCheck className="h-3.5 w-3.5" />
            服务端会校验角色白名单
          </p>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={saving}
            >
              取消
            </Button>
            <Button
              type="button"
              onClick={() => void handleSubmit()}
              disabled={saving || !changed}
            >
              {saving && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
              保存
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
