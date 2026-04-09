// server/db/client.ts
/**
 * Prisma Client 单例
 * 
 * 防止在开发环境(Next.js)热重载时创建无数个 Prisma Client 实例
 * 从而导致数据库连接数耗尽 (Too many connections)
 */

import { PrismaClient } from '@prisma/client'

// 声明全局变量类型
const globalForPrisma = globalThis as unknown as {
  prisma: PrismaClient | undefined
}

// 核心逻辑：如果是第一次加载，就 new 一个。如果全局变量里已经有了，就复用。
export const prisma = globalForPrisma.prisma ?? new PrismaClient()

// 在非生产环境下，将实例挂载到 global 对象上
// 因为 Next.js 开发环境热更新时会清除模块缓存，但不会清除 global 对象
if (process.env.NODE_ENV !== 'production') {
  globalForPrisma.prisma = prisma
}