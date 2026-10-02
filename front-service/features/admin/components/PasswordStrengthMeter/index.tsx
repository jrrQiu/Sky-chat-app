// features/admin/components/PasswordStrengthMeter/index.tsx
import {
  evaluatePasswordStrength,
  PASSWORD_CLASS_TOTAL,
  type PasswordStrength,
} from '@/features/admin/types'

interface PasswordStrengthMeterProps {
  password: string
  /** 已经算过一遍时可以直接传进来，避免重复计算。 */
  strength?: PasswordStrength
}

const BAR_COLORS = [
  'bg-red-400',
  'bg-amber-400',
  'bg-blue-400',
  'bg-green-500',
]

function barColor(strength: PasswordStrength): string {
  if (strength.length === 0) return 'bg-gray-200 dark:bg-gray-800'
  if (!strength.ok) return 'bg-red-400'
  return strength.label === '强' ? BAR_COLORS[3] : BAR_COLORS[2]
}

/**
 * 客户端密码强度提示。
 *
 * 只镜像服务端规则（≥12 位且 ≥3 类字符）用于提前提示，不做安全判断；
 * 真正的校验始终发生在服务端。
 */
export function PasswordStrengthMeter({ password, strength }: PasswordStrengthMeterProps) {
  const result = strength ?? evaluatePasswordStrength(password)
  const filled = password.length === 0 ? 0 : result.ok ? result.classes : 1

  return (
    <div className="space-y-1">
      <div className="flex gap-1">
        {Array.from({ length: PASSWORD_CLASS_TOTAL }, (_, index) => (
          <span
            key={index}
            className={`h-1 flex-1 rounded-full ${
              index < filled ? barColor(result) : 'bg-gray-200 dark:bg-gray-800'
            }`}
          />
        ))}
      </div>
      <p
        className={`text-xs ${
          password.length === 0
            ? 'text-gray-400 dark:text-gray-500'
            : result.ok
              ? 'text-green-600 dark:text-green-400'
              : 'text-amber-600 dark:text-amber-400'
        }`}
      >
        {result.label}：{result.hint}
      </p>
    </div>
  )
}
