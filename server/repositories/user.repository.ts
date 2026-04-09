// server/repositories/user.repository.ts
import { prisma } from '../db/client'
//用户存储类
export class UserRepository {
  /**
   * 根据 ID 查找用户
   */
  static async findById(id: string) {
    return prisma.user.findUnique({
      where: { id },
      // 为了安全，我们只返回需要的字段，不返回密码等敏感信息
      select: {
        id: true,
        name: true,
        email: true,
        image: true,
        apiKey: true, // 核心：获取用户自定义的大模型 API Key
      },
    })
  }
}