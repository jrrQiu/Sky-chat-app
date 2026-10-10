/**
 * 把光标送回输入框。
 *
 * "需要补充信息"的卡片出现在消息流里，而输入框在页面底部：用户看完问题后还要自己去找输入框、
 * 点一下、再打字。这个动作只做一件事——把焦点和光标位置交给输入框。
 *
 * 用 id 而不是 ref/store 是有意的：输入框在整个应用里是单例（每个会话只有一个），
 * 走 ref 需要把引用从 ChatInputUI 一路穿过 store 传到消息组件，为一次 focus 引入的
 * 耦合比它解决的问题多。找不到元素时静默返回：焦点只是便利，不是功能。
 */
export const CHAT_COMPOSER_INPUT_ID = 'chat-composer-input'

export function focusComposer(): void {
  if (typeof document === 'undefined') return
  const element = document.getElementById(CHAT_COMPOSER_INPUT_ID)
  if (!(element instanceof HTMLInputElement) && !(element instanceof HTMLTextAreaElement)) {
    return
  }
  element.focus()
  const end = element.value.length
  try {
    element.setSelectionRange(end, end)
  } catch {
    // 某些 input type 不支持选区；聚焦本身已经达到目的。
  }
}
