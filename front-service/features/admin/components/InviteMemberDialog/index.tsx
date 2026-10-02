// features/admin/components/InviteMemberDialog/index.tsx
import { useEffect, useState, type FormEvent } from 'react'
import { Check, Copy, Loader2, MailPlus } from 'lucide-react'
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
import { RoleMultiSelect } from '@/features/admin/components/RoleMultiSelect'
import type { InviteActionResult } from '@/features/admin/store/admin.store'
import type { CreateInvitationRequest } from '@/features/admin/services/admin.service'
import { formatDateTime } from '@/features/admin/types'

interface InviteMemberDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  roles: string[]
  submitting: boolean
  /** 从成员行发起的邀请：邮箱与角色已确定，只让管理员确认 TTL。 */
  preset?: { email: string; name?: string | null; roles?: string[] | null } | null
  onSubmit: (payload: CreateInvitationRequest) => Promise<InviteActionResult>
}

const TTL_OPTIONS = [
  { value: 24, label: '24 小时' },
  { value: 48, label: '48 小时' },
  { value: 72, label: '72 小时' },
  { value: 168, label: '7 天' },
]

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

/**
 * 复制邀请链接。
 *
 * navigator.clipboard 只在安全上下文可用，失败时退回到 execCommand，
 * 保证在 http 内网部署下依然能复制。
 */
async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // 继续尝试回退方案
  }

  try {
    const area = document.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', '')
    area.style.position = 'fixed'
    area.style.top = '-1000px'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(area)
    return ok
  } catch {
    return false
  }
}

export function InviteMemberDialog({
  open,
  onOpenChange,
  roles,
  submitting,
  preset,
  onSubmit,
}: InviteMemberDialogProps) {
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [selectedRoles, setSelectedRoles] = useState<string[]>([])
  const [ttlHours, setTtlHours] = useState(72)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState(false)
  const [result, setResult] = useState<{
    email: string
    inviteUrl: string | null
    expiresAt?: string | null
  } | null>(null)

  useEffect(() => {
    if (!open) return
    setEmail(preset?.email ?? '')
    setName(preset?.name ?? '')
    setSelectedRoles(preset?.roles?.filter(Boolean) ?? [])
    setTtlHours(72)
    setError('')
    setCopied(false)
    setResult(null)
  }, [open, preset])

  // 复制状态 2 秒后自动复位，避免按钮一直停在"已复制"。
  useEffect(() => {
    if (!copied) return
    const timer = window.setTimeout(() => setCopied(false), 2000)
    return () => window.clearTimeout(timer)
  }, [copied])

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')

    if (!EMAIL_PATTERN.test(email.trim())) {
      setError('请输入有效的邮箱地址')
      return
    }
    if (selectedRoles.length === 0) {
      setError('请至少选择一个角色')
      return
    }

    const response = await onSubmit({
      email: email.trim(),
      name: name.trim() || undefined,
      roles: selectedRoles,
      ttlHours,
    })

    if (!response.ok) {
      setError(response.message)
      return
    }

    setResult({
      email: email.trim(),
      inviteUrl: response.inviteUrl ?? null,
    })
  }

  const handleCopy = async () => {
    if (!result?.inviteUrl) return
    const ok = await copyToClipboard(result.inviteUrl)
    setCopied(ok)
    if (!ok) {
      setError('复制失败，请手动选中链接复制')
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !submitting && onOpenChange(next)}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>发送邀请</DialogTitle>
          <DialogDescription>
            受邀人通过邀请链接自行设置密码，链接过期或撤销后立即失效。
          </DialogDescription>
        </DialogHeader>

        {result ? (
          <div className="space-y-3">
            <p className="flex items-center gap-2 rounded-md bg-green-50 px-3 py-2 text-sm text-green-700 dark:bg-green-950/30 dark:text-green-400">
              <Check className="h-4 w-4 shrink-0" />
              已向 {result.email} 发送邀请
            </p>

            {result.inviteUrl ? (
              <div className="space-y-1.5">
                <label htmlFor="invite-link-field" className="text-xs text-gray-500 dark:text-gray-400">
                  邀请链接
                </label>
                <div className="flex gap-2">
                  <Input
                    id="invite-link-field"
                    readOnly
                    value={result.inviteUrl}
                    onFocus={(event) => event.currentTarget.select()}
                    className="font-mono text-xs"
                  />
                  <Button type="button" variant="outline" onClick={() => void handleCopy()}>
                    {copied ? (
                      <Check className="mr-1.5 h-3.5 w-3.5" />
                    ) : (
                      <Copy className="mr-1.5 h-3.5 w-3.5" />
                    )}
                    {copied ? '已复制' : '复制'}
                  </Button>
                </div>
                <p className="text-xs text-gray-400 dark:text-gray-500">
                  链接同时已通过邮件发送给受邀人，也可以直接转发。
                </p>
              </div>
            ) : (
              <p className="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:bg-amber-950/30 dark:text-amber-400">
                服务端未返回邀请链接，请提醒受邀人查收邮件。
              </p>
            )}

            {error && (
              <p className="text-destructive text-sm" role="alert">
                {error}
              </p>
            )}

            <DialogFooter>
              <Button type="button" onClick={() => onOpenChange(false)}>
                完成
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-3">
            <div className="space-y-1.5">
              <label htmlFor="invite-member-email" className="text-xs text-gray-500 dark:text-gray-400">
                邮箱
              </label>
              <Input
                id="invite-member-email"
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="name@company.com"
                autoComplete="off"
                required
                disabled={submitting || Boolean(preset)}
              />
            </div>

            <div className="space-y-1.5">
              <label htmlFor="invite-member-name" className="text-xs text-gray-500 dark:text-gray-400">
                姓名（选填）
              </label>
              <Input
                id="invite-member-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="受邀人接受后可自行修改"
                disabled={submitting}
              />
            </div>

            <div className="space-y-1.5">
              <span className="text-xs text-gray-500 dark:text-gray-400">角色</span>
              <RoleMultiSelect
                idPrefix="invite-member-role"
                roles={roles}
                value={selectedRoles}
                onChange={setSelectedRoles}
                disabled={submitting}
              />
            </div>

            <div className="space-y-1.5">
              <label htmlFor="invite-member-ttl" className="text-xs text-gray-500 dark:text-gray-400">
                有效期
              </label>
              <select
                id="invite-member-ttl"
                value={ttlHours}
                onChange={(event) => setTtlHours(Number(event.target.value))}
                disabled={submitting}
                className="h-8 w-full rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50 dark:bg-input/30"
              >
                {TTL_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>

            {preset && (
              <p className="rounded-md bg-gray-50 px-3 py-2 text-xs text-gray-500 dark:bg-gray-900 dark:text-gray-400">
                该邀请不会创建新账号，而是在现有成员上重新签发一次性链接。
              </p>
            )}

            {error && (
              <p className="text-destructive text-sm" role="alert">
                {error}
              </p>
            )}

            <DialogFooter className="gap-2 sm:justify-between">
              <p className="text-xs text-gray-400 dark:text-gray-500">
                过期时间：{formatDateTime(new Date(Date.now() + ttlHours * 3600_000).toISOString())}
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
                    <MailPlus className="mr-1.5 h-4 w-4" />
                  )}
                  发送邀请
                </Button>
              </div>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  )
}
