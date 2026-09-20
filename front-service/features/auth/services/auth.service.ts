import { apiJson } from '@/lib/api/client'
import type { AuthSession } from '@/lib/auth/session'

interface Credentials {
  email: string
  password: string
  name?: string
}

export function login(credentials: Credentials): Promise<AuthSession> {
  return apiJson<AuthSession>('/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify(credentials),
  })
}

export function register(credentials: Credentials): Promise<AuthSession> {
  return apiJson<AuthSession>('/v1/auth/register', {
    method: 'POST',
    body: JSON.stringify(credentials),
  })
}
