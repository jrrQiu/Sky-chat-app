// features/auth/hooks/use-auth.ts
//编写统一的 Auth Hook
import { useSession, signOut } from 'next-auth/react'

export function useAuth() {
  // 调用 NextAuth 提供的原生 hook
  const { data: session, status } = useSession()
  
  const isLoading = status === 'loading'
  const isAuthenticated = status === 'authenticated'
  
  // 如果没加载中，且没认证，就需要弹窗
  const showLoginDialog = status === 'unauthenticated'
  
  const user = session?.user ? {
    id: session.user.id || '',
    email: session.user.email,
    name: session.user.name,
    image: session.user.image,
  } : null

  const logout = async () => {
    await signOut({ callbackUrl: '/' })
  }

  return {
    isAuthenticated,
    user,
    isLoading,
    showLoginDialog,
    logout,
  }
}