// features/auth/components/LoginDialog/index.tsx
'use client'

import { signIn } from 'next-auth/react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Github } from 'lucide-react'

interface LoginDialogProps {
  open: boolean
}

export function LoginDialog({ open }: LoginDialogProps) {
  const handleGithubLogin = () => {
    // 调用 NextAuth 提供的 signIn，指定使用 github 策略
    signIn('github', { callbackUrl: '/' })
  }

  return (
    <Dialog open={open}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="text-center text-2xl font-bold">欢迎来到 Sky-Chat</DialogTitle>
          <DialogDescription className="text-center">
            请登录以继续使用 AI 智能助手
          </DialogDescription>
        </DialogHeader>
        
        <div className="flex flex-col space-y-4 py-4">
          <Button 
            variant="outline" 
            className="w-full bg-[#24292F] text-white hover:bg-[#24292F]/90 hover:text-white"
            onClick={handleGithubLogin}
          >
            使用 GitHub 账号登录
          </Button>
          
          <div className="text-center text-xs text-gray-500">
            登录即代表您同意我们的服务条款和隐私政策
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}