/*
 * Undo the page scroll iOS Safari leaves behind when the keyboard closes.
 *
 * Focusing a field near the bottom of the screen -- a set input, the chat
 * composer -- makes Safari scroll the whole document up so the field clears
 * the keyboard. When the keyboard goes away, Safari frequently doesn't
 * scroll back. The signed-in shell is exactly one screen tall (`h-dvh`) and
 * scrolls inside <main>, so that leftover offset has nowhere to go: the app
 * stays shifted up by the keyboard's height, a blank strip sits where the
 * keyboard was, and the controls under it can't be reached until a zoom
 * forces Safari to re-lay out.
 *
 * So when focus leaves a field without landing on another one, or the
 * visual viewport grows back after the keyboard closes, snap the document
 * back to the top. Only when the document can't legitimately scroll: pages
 * taller than the screen (login, signup) keep whatever scroll the user gave
 * them.
 */
function isEditable(el: Element | null): boolean {
  if (!el) return false
  if (el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) return true
  if (el instanceof HTMLInputElement) return true
  return (el as HTMLElement).isContentEditable === true
}

function resetIfStale(win: Window) {
  const doc = win.document
  if (isEditable(doc.activeElement)) return
  if (win.scrollY === 0 && win.scrollX === 0) return
  // A document taller than the screen is allowed to be scrolled.
  if (doc.documentElement.scrollHeight > win.innerHeight + 1) return
  win.scrollTo(0, 0)
}

export function installKeyboardScrollReset(win: Window = window): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined
  // Deferred: on focusout, activeElement is still in flux until the next
  // field (if any) has taken focus.
  const schedule = () => {
    clearTimeout(timer)
    timer = setTimeout(() => resetIfStale(win), 50)
  }
  win.document.addEventListener('focusout', schedule)
  win.visualViewport?.addEventListener('resize', schedule)
  return () => {
    clearTimeout(timer)
    win.document.removeEventListener('focusout', schedule)
    win.visualViewport?.removeEventListener('resize', schedule)
  }
}
