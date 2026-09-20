// features/auth/components/AuthGuard/index.tsx
import { useAuth } from '@/features/auth/hooks/use-auth'
import { LoginDialog } from '../LoginDialog'
import { Loader2 } from 'lucide-react'

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { isLoading, isAuthenticated, showLoginDialog } = useAuth()

  if (isLoading) {
    return (
      <div className="flex h-screen w-full items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
      </div>
    )
  }

  if (!isAuthenticated) {
    return (
      <>
        <div className="pointer-events-none h-screen w-full bg-white opacity-20 blur-sm dark:bg-gray-900" />
        <LoginDialog
          open={showLoginDialog}
          onOpenChange={() => {}}
          onSuccess={() => window.location.reload()}
        />
      </>
    )
  }

  return <>{children}</>
}
