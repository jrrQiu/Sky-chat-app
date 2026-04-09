// app/api/auth/[...nextauth]/route.ts
import NextAuth from 'next-auth'
import { authOptions } from '@/server/auth/auth'

// 初始化 NextAuth 处理器
const handler = NextAuth(authOptions)

// App Router 要求同时导出 GET 和 POST
export { handler as GET, handler as POST }