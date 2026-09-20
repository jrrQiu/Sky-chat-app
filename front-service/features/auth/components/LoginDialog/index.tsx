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
import { login, register } from '@/features/auth/services/auth.service'
import { useAuthStore } from '@/features/auth/store/auth.store'

interface LoginDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess: () => void
}

type AuthMode = 'login' | 'register'

export function LoginDialog({
  open,
  onOpenChange,
  onSuccess,
}: LoginDialogProps) {
  const [mode, setMode] = useState<AuthMode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const setSession = useAuthStore((state) => state.login)

  const reset = () => {
    setEmail('')
    setPassword('')
    setName('')
    setError('')
    setLoading(false)
  }

  const handleModeChange = (nextMode: AuthMode) => {
    setMode(nextMode)
    setError('')
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    setLoading(true)

    try {
      if (mode === 'register') {
        const session = await register({ email, password, name })
        setSession(session.token, session.user)
      } else {
        const session = await login({ email, password })
        setSession(session.token, session.user)
      }

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
            {mode === 'login' ? '登录你的账号' : '创建一个新账号'}
          </DialogDescription>
        </DialogHeader>

        <div className="bg-muted flex rounded-lg p-1">
          <Button
            type="button"
            variant={mode === 'login' ? 'default' : 'ghost'}
            className="flex-1"
            onClick={() => handleModeChange('login')}
          >
            登录
          </Button>
          <Button
            type="button"
            variant={mode === 'register' ? 'default' : 'ghost'}
            className="flex-1"
            onClick={() => handleModeChange('register')}
          >
            注册
          </Button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          {mode === 'register' && (
            <Input
              type="text"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="昵称"
              required
              disabled={loading}
            />
          )}
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
            minLength={6}
            disabled={loading}
          />

          {error && (
            <p className="text-destructive text-sm" role="alert">
              {error}
            </p>
          )}

          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? '请稍候...' : mode === 'login' ? '登录' : '注册并登录'}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  )
}
