// features/admin/components/CreateMemberDialog/index.tsx
import { useEffect, useState, type FormEvent } from 'react'
import { Loader2, UserPlus } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { PasswordStrengthMeter } from '@/features/admin/components/PasswordStrengthMeter'
import { RoleMultiSelect } from '@/features/admin/components/RoleMultiSelect'
import type { ActionResult } from '@/features/admin/store/admin.store'
import type { CreateUserRequest } from '@/features/admin/services/admin.service'
import { evaluatePasswordStrength, PASSWORD_MIN_LENGTH } from '@/features/admin/types'

interface CreateMemberDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** 可分配角色白名单。 */
  roles: string[]
  submitting: boolean
  onSubmit: (payload: CreateUserRequest) => Promise<ActionResult>
}

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export function CreateMemberDialog({
  open,
  onOpenChange,
  roles,
  submitting,
  onSubmit,
}: CreateMemberDialogProps) {
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [selectedRoles, setSelectedRoles] = useState<string[]>([])
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')

  const strength = evaluatePasswordStrength(password)

  // 每次重新打开都回到干净状态，避免泄漏上一个账号的初始密码。
  useEffect(() => {
    if (!open) return
    setEmail('')
    setName('')
    setSelectedRoles([])
    setPassword('')
    setConfirm('')
    setError('')
  }, [open])

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')

    if (!EMAIL_PATTERN.test(email.trim())) {
      setError('请输入有效的邮箱地址')
      return
    }
    if (name.trim().length === 0) {
      setError('请填写成员姓名')
      return
    }
    if (selectedRoles.length === 0) {
      setError('请至少选择一个角色')
      return
    }
    if (!strength.ok) {
      setError(`初始密码不符合要求：${strength.hint}`)
      return
    }
    if (password !== confirm) {
      setError('两次输入的密码不一致')
      return
    }

    const result = await onSubmit({
      email: email.trim(),
      name: name.trim(),
      roles: selectedRoles,
      password,
    })

    if (!result.ok) {
      setError(result.message)
      return
    }
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !submitting && onOpenChange(next)}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>新建成员</DialogTitle>
          <DialogDescription>
            直接开通账号并设置初始密码，成员无需邮件激活即可登录。
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="space-y-1.5">
            <label htmlFor="create-member-email" className="text-xs text-gray-500 dark:text-gray-400">
              邮箱
            </label>
            <Input
              id="create-member-email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="name@company.com"
              autoComplete="off"
              required
              disabled={submitting}
            />
          </div>

          <div className="space-y-1.5">
            <label htmlFor="create-member-name" className="text-xs text-gray-500 dark:text-gray-400">
              姓名
            </label>
            <Input
              id="create-member-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="张三"
              required
              disabled={submitting}
            />
          </div>

          <div className="space-y-1.5">
            <span className="text-xs text-gray-500 dark:text-gray-400">角色</span>
            <RoleMultiSelect
              idPrefix="create-member-role"
              roles={roles}
              value={selectedRoles}
              onChange={setSelectedRoles}
              disabled={submitting}
            />
          </div>

          <div className="space-y-1.5">
            <label htmlFor="create-member-password" className="text-xs text-gray-500 dark:text-gray-400">
              初始密码
            </label>
            <Input
              id="create-member-password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder={`至少 ${PASSWORD_MIN_LENGTH} 位，包含大小写字母、数字或符号中的 3 类`}
              autoComplete="new-password"
              required
              disabled={submitting}
            />
            <PasswordStrengthMeter password={password} strength={strength} />
          </div>

          <div className="space-y-1.5">
            <label htmlFor="create-member-confirm" className="text-xs text-gray-500 dark:text-gray-400">
              确认密码
            </label>
            <Input
              id="create-member-confirm"
              type="password"
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
              placeholder="再次输入初始密码"
              autoComplete="new-password"
              required
              disabled={submitting}
            />
          </div>

          {error && (
            <p className="text-destructive text-sm" role="alert">
              {error}
            </p>
          )}

          <DialogFooter className="gap-2 sm:justify-between">
            <p className="text-xs text-gray-400 dark:text-gray-500">
              密码强度的判断以服务端规则为准
            </p>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={submitting}
              >
                取消
              </Button>
              <Button type="submit" disabled={submitting}>
                {submitting ? (
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                ) : (
                  <UserPlus className="mr-1.5 h-4 w-4" />
                )}
                创建成员
              </Button>
            </div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
