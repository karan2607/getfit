/*
 * Line icons for the bottom nav, replacing emoji.
 *
 * Emoji were not just off-brand. They are font glyphs, so they render in the
 * platform's own multi-colour bitmap and ignore `currentColor` entirely --
 * which meant the active tab's orange applied to the label and not to the
 * icon above it, and the bar looked the same whichever tab you were on. They
 * also differ per OS, and ❤️ and 🏋️ carry a variation selector that some
 * Android builds render as a dingbat box.
 *
 * One 24x24 grid, 1.5 stroke, round caps, no fills: the active state is then
 * a single colour change on both halves of the item.
 */
type IconProps = { className?: string }

const base = {
  width: 22,
  height: 22,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.5,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
}

export function HomeIcon(p: IconProps) {
  return (
    <svg {...base} {...p} aria-hidden="true">
      <path d="M3 10.5 12 3.5l9 7" />
      <path d="M5 9.8V20h14V9.8" />
      <path d="M9.5 20v-5.5h5V20" />
    </svg>
  )
}

export function WorkoutsIcon(p: IconProps) {
  return (
    <svg {...base} {...p} aria-hidden="true">
      <path d="M4 9v6M7 7.5v9M17 7.5v9M20 9v6" />
      <path d="M7 12h10" />
    </svg>
  )
}

export function ChatIcon(p: IconProps) {
  return (
    <svg {...base} {...p} aria-hidden="true">
      <path d="M20.5 11.5a7.5 7.5 0 0 1-10.9 6.7L4.5 19.5l1.3-4.1A7.5 7.5 0 1 1 20.5 11.5Z" />
      <path d="M9 11.5h6" />
    </svg>
  )
}

export function DietIcon(p: IconProps) {
  return (
    <svg {...base} {...p} aria-hidden="true">
      <path d="M4.5 10.5h15c0 5-3.4 9-7.5 9s-7.5-4-7.5-9Z" />
      <path d="M12 10.5c0-3 1.8-5.5 4.5-6.5" />
      <path d="M8.5 14.5h7" />
    </svg>
  )
}

export function HealthIcon(p: IconProps) {
  return (
    <svg {...base} {...p} aria-hidden="true">
      <path d="M3.5 12.5h3l2-4 2.5 7 2.5-5 1.8 3h5.2" />
    </svg>
  )
}

export function ProfileIcon(p: IconProps) {
  return (
    <svg {...base} {...p} aria-hidden="true">
      <circle cx="12" cy="8.5" r="3.5" />
      <path d="M5 20c0-3.6 3.1-5.5 7-5.5s7 1.9 7 5.5" />
    </svg>
  )
}
