// middleware.ts
import { getToken } from 'next-auth/jwt'
import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

export async function middleware(request: NextRequest) {
  // 获取用户的登录状态 Token
  const token = await getToken({ 
    req: request as any, 
    secret: process.env.NEXTAUTH_SECRET || process.env.AUTH_SECRET
  })
  
  const isLoggedIn = !!token
  const { pathname } = request.nextUrl

  // 1. 如果已登录，且访问的是首页 ("/")，直接重定向到聊天工作台 ("/chat")
  if (pathname === '/' && isLoggedIn) {
    return NextResponse.redirect(new URL('/chat', request.url))
  }

  // 2. 如果未登录，且强行访问 "/chat" 或其子页面，踢回首页
  if (pathname.startsWith('/chat') && !isLoggedIn) {
    return NextResponse.redirect(new URL('/', request.url))
  }

  return NextResponse.next()
}

// 告诉 Next.js 哪些路径需要被这个中间件拦截
export const config = {
  matcher: ['/', '/chat/:path*'],
}