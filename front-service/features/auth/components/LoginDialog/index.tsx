'use client'

import { FormEvent, useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { login } from '@/features/auth/services/auth.service'
import { useAuthStore } from '@/features/auth/store/auth.store'

interface LoginDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess: () => void
}

export function LoginDialog({
  open,
  onOpenChange,
  onSuccess,
}: LoginDialogProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const setSession = useAuthStore((state) => state.login)

  const reset = () => {
    setEmail('')
    setPassword('')
    setError('')
    setLoading(false)
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    setLoading(true)

    try {
      const session = await login({ email, password })
      setSession(session.token, session.user)

      reset()
      onOpenChange(false)
      onSuccess()
    } catch (requestError) {
      setError(
        requestError instanceof Error ? requestError.message : '请求失败，请稍后重试'
      )
    } finally {
      setLoading(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="text-center text-2xl font-bold">
            欢迎来到 Sky-Chat
          </DialogTitle>
          <DialogDescription className="text-center">
            登录你的账号
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-3">
          <Input
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="邮箱"
            required
            disabled={loading}
          />
          <Input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="密码"
            required
            disabled={loading}
          />

          {error && (
            <p className="text-destructive text-sm" role="alert">
              {error}
            </p>
          )}

          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? '请稍候...' : '登录'}
          </Button>

          {/* 自助注册已关闭，账号只能由管理员开通或通过邀请链接激活 */}
          <p className="text-center text-xs text-gray-500 dark:text-gray-400">
            账号由管理员开通，或使用邀请链接设置密码
          </p>
        </form>
      </DialogContent>
    </Dialog>
  )
}
