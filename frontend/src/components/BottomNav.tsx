import { NavLink } from 'react-router-dom'

const NAV_ITEMS = [
  { to: '/dashboard', icon: '🏠', label: 'Home' },
  { to: '/workouts', icon: '🏋️', label: 'Workouts' },
  { to: '/chat', icon: '💬', label: 'AI' },
  { to: '/diet', icon: '🥗', label: 'Diet' },
  { to: '/health', icon: '❤️', label: 'Health' },
  { to: '/profile', icon: '👤', label: 'Profile' },
]

export default function BottomNav() {
  return (
    <nav
      // Navy, and padded for the home indicator. There was NO
      // safe-area handling anywhere in this app, so on a notched
      // phone the last few pixels of this bar sat under the system
      // gesture area -- which is where the tap targets are.
      style={{ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}
      className="fixed bottom-0 left-0 right-0 z-50 bg-ink-600 border-t border-ink-500 flex items-stretch md:hidden"
    >
      {NAV_ITEMS.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          className={({ isActive }) =>
            `flex-1 flex flex-col items-center justify-center py-2 gap-0.5 text-xs font-semibold transition-colors
             ${isActive ? 'text-accent-500' : 'text-ink-200'}`
          }
        >
          <span className="text-xl leading-none">{item.icon}</span>
          <span>{item.label}</span>
        </NavLink>
      ))}
    </nav>
  )
}
