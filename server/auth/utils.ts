// server/auth/utils.ts
import { getServerSession } from 'next-auth/next'
import { authOptions } from './auth'

/**
 * 获取当前登录用户的 Session
 */
export async function getCurrentUser() {
  const session = await getServerSession(authOptions)
  return session?.user
}

/**
 * 获取当前登录用户的 ID，未登录则抛出错误
 */
export async function getCurrentUserId() {
  const user = await getCurrentUser()
  
  if (!user?.id) {
    throw new Error('Unauthorized')
  }
  
  return user.id
}