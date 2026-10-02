import { apiJson } from '@/lib/api/client'
import type { AuthSession } from '@/lib/auth/session'

interface Credentials {
  email: string
  password: string
}

// 自助注册已关闭：账号由管理员开通（POST /v1/admin/users）或通过邀请链接激活。
export function login(credentials: Credentials): Promise<AuthSession> {
  return apiJson<AuthSession>('/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify(credentials),
  })
}
