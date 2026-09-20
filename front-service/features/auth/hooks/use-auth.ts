import { useEffect } from 'react'
import { useAuthStore } from '@/features/auth/store/auth.store'

export function useAuth() {
  const { status, user, logout, loadFromStorage } = useAuthStore()

  useEffect(() => {
    loadFromStorage()
  }, [loadFromStorage])

  const isLoading = status === 'loading'
  const isAuthenticated = status === 'authenticated'
  const showLoginDialog = status === 'unauthenticated'

  return {
    isAuthenticated,
    user,
    isLoading,
    showLoginDialog,
    logout,
  }
}
