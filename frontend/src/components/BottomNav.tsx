import { NavLink } from 'react-router-dom'
import {
  ChatIcon, DietIcon, HealthIcon, HomeIcon, ProfileIcon, WorkoutsIcon,
} from './NavIcons'

const NAV_ITEMS = [
  { to: '/dashboard', Icon: HomeIcon, label: 'Home' },
  { to: '/workouts', Icon: WorkoutsIcon, label: 'Workouts' },
  { to: '/chat', Icon: ChatIcon, label: 'AI' },
  { to: '/diet', Icon: DietIcon, label: 'Diet' },
  { to: '/health', Icon: HealthIcon, label: 'Health' },
  { to: '/profile', Icon: ProfileIcon, label: 'Profile' },
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
          <item.Icon />
          <span>{item.label}</span>
        </NavLink>
      ))}
    </nav>
  )
}
