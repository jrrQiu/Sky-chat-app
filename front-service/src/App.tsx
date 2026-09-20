import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { LandingPage } from '@/components/LandingPage'
import { AuthGuard } from '@/features/auth/components/AuthGuard'
import { ChatConversationPage } from '@/src/pages/ChatConversationPage'
import { ChatRedirectPage } from '@/src/pages/ChatRedirectPage'

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
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
