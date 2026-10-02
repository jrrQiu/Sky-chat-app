// src/pages/InviteAcceptPage.tsx
// 公开页面：受邀人未登录也能访问，通过 ?token= 校验邀请并设置密码。
import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Check, Loader2, MailCheck, ShieldX } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { PasswordStrengthMeter } from '@/features/admin/components/PasswordStrengthMeter'
import { RoleBadges } from '@/features/admin/components/MemberTable'
import {
  acceptInvitation,
  previewInvitation,
  type InvitationPreview,
} from '@/features/admin/services/admin.service'
import {
  adminErrorMessage,
  evaluatePasswordStrength,
  formatDateTime,
  formatRelativeTime,
  invitationInvalidReason,
  PASSWORD_MIN_LENGTH,
} from '@/features/admin/types'
import { useAuthStore } from '@/features/auth/store/auth.store'

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4 py-10 dark:bg-gray-900">
      <div className="w-full max-w-md">
        <p className="mb-4 text-center text-lg font-semibold">
          <span className="bg-gradient-to-r from-blue-400 to-blue-600 bg-clip-text text-transparent">
            Sky Chat
          </span>
        </p>
        <div className="rounded-xl border border-gray-200 bg-white p-6 dark:border-gray-800 dark:bg-gray-950">
          {children}
        </div>
        <p className="mt-4 text-center text-xs text-gray-400 dark:text-gray-500">
          <Link to="/" className="underline underline-offset-4 hover:text-gray-600 dark:hover:text-gray-300">
            返回首页
          </Link>
        </p>
      </div>
    </div>
  )
}

export function InviteAcceptPage() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const saveSession = useAuthStore((state) => state.login)
  const token = searchParams.get('token')?.trim() ?? ''

  const [preview, setPreview] = useState<InvitationPreview | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const strength = evaluatePasswordStrength(password)

  useEffect(() => {
    let cancelled = false

    if (!token) {
      setLoading(false)
      setLoadError('邀请链接缺少 token 参数，请确认链接是否完整')
      return
    }

    setLoading(true)
    setLoadError(null)

    previewInvitation(token)
      .then((data) => {
        if (cancelled) return
        setPreview(data)
        setName(data.name ?? '')
        setLoading(false)
      })
      .catch((requestError: unknown) => {
        if (cancelled) return
        setPreview(null)
        setLoadError(
          requestError instanceof Error && requestError.message
            ? adminErrorMessage(requestError.message)
            : '邀请链接校验失败，请稍后重试'
        )
        setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [token])

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')

    if (!strength.ok) {
      setError(`密码不符合要求：${strength.hint}`)
      return
    }
    if (password !== confirm) {
      setError('两次输入的密码不一致')
      return
    }

    setSubmitting(true)
    try {
      const session = await acceptInvitation({
        token,
        password,
        name: name.trim() || undefined,
      })
      // 接受邀请的响应与登录同形，直接复用会话存储即可，无需再登录一次。
      saveSession(session.token, session.user)
      toast.success('密码设置成功，已为你登录')
      navigate('/chat', { replace: true })
    } catch (requestError) {
      setError(
        requestError instanceof Error && requestError.message
          ? adminErrorMessage(requestError.message)
          : '设置密码失败，请稍后重试'
      )
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) {
    return (
      <Shell>
        <div className="flex items-center justify-center gap-2 py-6 text-sm text-gray-400">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在校验邀请链接…
        </div>
      </Shell>
    )
  }

  const invalid = loadError !== null || preview?.valid === false

  if (invalid) {
    return (
      <Shell>
        <div className="text-center">
          <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-red-50 text-red-600 dark:bg-red-950/40 dark:text-red-400">
            <ShieldX className="h-5 w-5" />
          </div>
          <h1 className="mt-3 text-base font-semibold text-gray-900 dark:text-gray-100">
            邀请链接不可用
          </h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            {loadError ?? invitationInvalidReason(preview?.expiresAt)}
          </p>
          <div className="mt-4 flex justify-center gap-2">
            <Button asChild variant="outline">
              <Link to="/">返回首页</Link>
            </Button>
          </div>
        </div>
      </Shell>
    )
  }

  const expiry = formatRelativeTime(preview?.expiresAt)

  return (
    <Shell>
      <div className="space-y-4">
        <div className="text-center">
          <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-blue-50 text-blue-600 dark:bg-blue-950/40 dark:text-blue-400">
            <MailCheck className="h-5 w-5" />
          </div>
          <h1 className="mt-3 text-base font-semibold text-gray-900 dark:text-gray-100">
            设置你的登录密码
          </h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            接受邀请后账号立即生效
          </p>
        </div>

        <dl className="space-y-2 rounded-lg bg-gray-50 px-3 py-2.5 text-sm dark:bg-gray-900">
          <div className="flex items-center justify-between gap-3">
            <dt className="text-xs text-gray-400 dark:text-gray-500">邮箱</dt>
            <dd className="truncate text-gray-800 dark:text-gray-200">{preview?.email}</dd>
          </div>
          <div className="flex items-start justify-between gap-3">
            <dt className="shrink-0 text-xs text-gray-400 dark:text-gray-500">角色</dt>
            <dd className="flex justify-end">
              <RoleBadges roles={preview?.roles} />
            </dd>
          </div>
          {preview?.expiresAt && (
            <div className="flex items-center justify-between gap-3">
              <dt className="text-xs text-gray-400 dark:text-gray-500">有效期</dt>
              <dd className="text-xs text-gray-600 dark:text-gray-300">
                {formatDateTime(preview.expiresAt)}
                {expiry && `（${expiry}）`}
              </dd>
            </div>
          )}
        </dl>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="space-y-1.5">
            <label htmlFor="invite-name" className="text-xs text-gray-500 dark:text-gray-400">
              姓名（选填）
            </label>
            <Input
              id="invite-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="留空则使用邀请中的姓名"
              disabled={submitting}
            />
          </div>

          <div className="space-y-1.5">
            <label htmlFor="invite-password" className="text-xs text-gray-500 dark:text-gray-400">
              密码
            </label>
            <Input
              id="invite-password"
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
            <label htmlFor="invite-confirm" className="text-xs text-gray-500 dark:text-gray-400">
              确认密码
            </label>
            <Input
              id="invite-confirm"
              type="password"
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
              placeholder="再次输入密码"
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

          <Button type="submit" className="w-full" disabled={submitting}>
            {submitting ? (
              <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
            ) : (
              <Check className="mr-1.5 h-4 w-4" />
            )}
            设置密码并登录
          </Button>

          <p className="text-center text-xs text-gray-400 dark:text-gray-500">
            链接一次性使用，设置成功后即失效
          </p>
        </form>
      </div>
    </Shell>
  )
}
