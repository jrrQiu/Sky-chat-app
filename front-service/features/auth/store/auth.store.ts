import { create } from 'zustand'
import {
  clearSession,
  getAccessToken,
  getStoredUser,
  saveSession,
  type AuthUser,
} from '@/lib/auth/session'

export type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated'

interface AuthState {
  status: AuthStatus
  user: AuthUser | null
  login: (token: string, user: AuthUser) => void
  logout: () => void
  loadFromStorage: () => void
}

function resolveStoredSession(): {
  status: AuthStatus
  user: AuthUser | null
} {
  const token = getAccessToken()
  const user = getStoredUser()

  if (!token || !user) {
    clearSession()
    return { status: 'unauthenticated', user: null }
  }

  return { status: 'authenticated', user }
}

export const useAuthStore = create<AuthState>((set) => ({
  ...resolveStoredSession(),
  login: (token, user) => {
    saveSession({ token, user })
    set({ status: 'authenticated', user })
  },
  logout: () => {
    clearSession()
    set({ status: 'unauthenticated', user: null })
  },
  loadFromStorage: () => {
    set(resolveStoredSession())
  },
}))
