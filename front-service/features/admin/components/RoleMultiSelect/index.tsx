// features/admin/components/RoleMultiSelect/index.tsx
import { roleLabel } from '@/features/admin/types'

interface RoleMultiSelectProps {
  /** 可分配的完整白名单。 */
  roles: string[]
  value: string[]
  onChange: (roles: string[]) => void
  disabled?: boolean
  /**
   * 目标当前持有的角色。用于"改角色"场景：即使某个角色已不在白名单里，
   * 也要显示出来，否则保存时会静默丢掉它。
   */
  heldRoles?: string[]
  idPrefix: string
}

/**
 * 角色多选。
 *
 * 用一排可按下的按钮而不是原生 select multiple：角色数量不多，
 * 全部平铺出来比让用户按住 Ctrl 点选更直观。
 */
export function RoleMultiSelect({
  roles,
  value,
  onChange,
  disabled = false,
  heldRoles = [],
  idPrefix,
}: RoleMultiSelectProps) {
  const options: string[] = []
  for (const role of [...roles, ...heldRoles]) {
    if (!options.includes(role)) options.push(role)
  }

  const toggle = (role: string) => {
    if (disabled) return
    onChange(value.includes(role) ? value.filter((item) => item !== role) : [...value, role])
  }

  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="角色">
      {options.map((role) => {
        const selected = value.includes(role)
        const outsideWhitelist = !roles.includes(role)
        return (
          <button
            key={role}
            id={`${idPrefix}-${role}`}
            type="button"
            aria-pressed={selected}
            disabled={disabled}
            onClick={() => toggle(role)}
            title={outsideWhitelist ? `${role}（不在可分配白名单内）` : role}
            className={`rounded-full border px-2.5 py-1 text-xs transition-colors disabled:opacity-50 ${
              selected
                ? 'border-blue-300 bg-blue-50 text-blue-700 dark:border-blue-800 dark:bg-blue-950/40 dark:text-blue-400'
                : 'border-gray-200 text-gray-500 hover:border-gray-300 hover:text-gray-700 dark:border-gray-800 dark:text-gray-400 dark:hover:text-gray-200'
            } ${outsideWhitelist ? 'border-dashed' : ''}`}
          >
            {roleLabel(role)}
          </button>
        )
      })}
    </div>
  )
}
