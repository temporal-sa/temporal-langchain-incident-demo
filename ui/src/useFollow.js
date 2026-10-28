import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

const NEAR_BOTTOM_PX = 48

/**
 * Keep a scrollable panel pinned to its newest content, without ever scrolling the page.
 *
 * - Only the panel's own scrollTop is changed. Nothing here calls scrollIntoView,
 *   which would also scroll the window and pull you away from what you are
 *   looking at.
 * - Following pauses as soon as the user scrolls up inside the panel, and resumes
 *   when they scroll back to the bottom or call jump().
 * - While paused (or switched off), new items are counted so the UI can offer a
 *   "jump to latest" control.
 *
 * Usage: const { ref, unseen, jump } = useFollow(items.length, { enabled })
 *        <div ref={ref} style={{ overflow: 'auto' }}>...</div>
 */
export function useFollow(count, { enabled = true } = {}) {
  const [el, setEl] = useState(null)
  const [unseen, setUnseen] = useState(0)
  const atBottom = useRef(true)
  const lastCount = useRef(0)

  useEffect(() => {
    if (!el) return undefined
    const onScroll = () => {
      atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX
      if (atBottom.current) setUnseen(0)
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [el])

  useLayoutEffect(() => {
    if (!el) return
    const added = count - lastCount.current
    lastCount.current = count
    if (added <= 0) return
    if (enabled && atBottom.current) {
      el.scrollTop = el.scrollHeight
    } else {
      setUnseen((n) => n + added)
    }
  }, [count, el, enabled])

  const jump = useCallback(() => {
    if (!el) return
    el.scrollTop = el.scrollHeight
    atBottom.current = true
    setUnseen(0)
  }, [el])

  return { ref: setEl, unseen, jump }
}

/** A boolean preference remembered in localStorage (falls back to memory). */
export function usePreference(key, initial) {
  const [value, setValue] = useState(() => {
    try {
      const stored = window.localStorage.getItem(key)
      return stored === null ? initial : stored === 'true'
    } catch {
      return initial
    }
  })
  const update = useCallback(
    (next) => {
      setValue(next)
      try {
        window.localStorage.setItem(key, String(next))
      } catch {
        /* storage unavailable: keep it for this session */
      }
    },
    [key],
  )
  return [value, update]
}
