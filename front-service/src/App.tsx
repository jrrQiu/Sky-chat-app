import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { LandingPage } from '@/components/LandingPage'
import { AuthGuard } from '@/features/auth/components/AuthGuard'
import { AdminUsersPage } from '@/src/pages/AdminUsersPage'
import { ApprovalsPage } from '@/src/pages/ApprovalsPage'
import { ChatConversationPage } from '@/src/pages/ChatConversationPage'
import { ChatRedirectPage } from '@/src/pages/ChatRedirectPage'
import { InviteAcceptPage } from '@/src/pages/InviteAcceptPage'

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route
          path="/chat"
          element={
            <AuthGuard>
              <ChatRedirectPage />
            </AuthGuard>
          }
        />
        <Route
          path="/chat/:conversationId"
          element={
            <AuthGuard>
              <ChatConversationPage />
            </AuthGuard>
          }
        />
        <Route
          path="/approvals"
          element={
            <AuthGuard>
              <ApprovalsPage />
            </AuthGuard>
          }
        />
        <Route
          path="/admin/users"
          element={
            <AuthGuard>
              <AdminUsersPage />
            </AuthGuard>
          }
        />
        {/* 邀请接受页必须能在未登录时访问，因此不套 AuthGuard */}
        <Route path="/invite" element={<InviteAcceptPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
