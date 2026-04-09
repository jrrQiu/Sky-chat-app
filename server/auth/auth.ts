// server/auth/auth.ts
//鉴权文件
import type { NextAuthOptions } from 'next-auth'
import { PrismaAdapter } from '@next-auth/prisma-adapter'
import GoogleProvider from 'next-auth/providers/google'
import GitHubProvider from 'next-auth/providers/github'
import CredentialsProvider from 'next-auth/providers/credentials'
import bcrypt from 'bcryptjs'
import { prisma } from '@/server/db/client' 

export const authOptions: NextAuthOptions = {
  // 1. 接入 Prisma Adapter，让 NextAuth 知道怎么读写数据库
  adapter: PrismaAdapter(prisma),
  
  // 2. 配置登录策略 (Providers)
  providers: [
    // GitHub 登录
    GitHubProvider({
      clientId: process.env.GITHUB_CLIENT_ID!,
      clientSecret: process.env.GITHUB_CLIENT_SECRET!,
    }),
    // Google 登录
    GoogleProvider({
      clientId: process.env.GOOGLE_CLIENT_ID!,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET!,
    }),
    // 账号密码登录
    CredentialsProvider({
      name: 'credentials',
      credentials: {
        email: { label: 'Email', type: 'email' },
        password: { label: 'Password', type: 'password' },
      },
      async authorize(credentials) {
        if (!credentials?.email || !credentials?.password) return null

        // 去数据库找用户
        const user = await prisma.user.findUnique({
          where: { email: credentials.email }
        })

        // 校验密码
        if (!user || !user.password) return null
        const isPasswordValid = await bcrypt.compare(credentials.password, user.password)

        if (!isPasswordValid) return null
        return user
      }
    })
  ],

  // 3. 核心机制：使用 JWT 策略（在 Serverless/Edge 环境最适合）
  session: {
    strategy: 'jwt',
  },

  // 4. 回调钩子 (Callbacks) 
  callbacks: {
    // 把数据库里的 userId 塞进 JWT token 中
    async jwt({ token, user }) {
      if (user) {
        token.id = user.id
      }
      return token
    },
    // 把 token 里的信息暴露给前端 session
    async session({ session, token }) {
      if (token && session.user) {
        session.user.id = token.id as string
      }
      return session
    }
  },

  // 5. 自定义登录页面的路径（替换 NextAuth 丑陋的默认登录页）
  pages: {
    signIn: '/auth/signin',
  },
}