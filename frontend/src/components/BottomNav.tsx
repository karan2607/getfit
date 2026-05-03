import { NavLink } from 'react-router-dom'

const NAV_ITEMS = [
  { to: '/dashboard', icon: '🏠', label: 'Home' },
  { to: '/workouts', icon: '🏋️', label: 'Workouts' },
  { to: '/chat', icon: '💬', label: 'AI' },
  { to: '/diet', icon: '🥗', label: 'Diet' },
  { to: '/profile', icon: '👤', label: 'Profile' },
]

export default function BottomNav() {
  return (
    <nav className="fixed bottom-0 left-0 right-0 z-50 bg-[#3E4A3F] border-t border-[#2e3830] flex items-stretch md:hidden">
      {NAV_ITEMS.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          className={({ isActive }) =>
            `flex-1 flex flex-col items-center justify-center py-2 gap-0.5 text-xs font-semibold transition-colors
             ${isActive ? 'text-brand-500' : 'text-gray-300'}`
          }
        >
          <span className="text-xl leading-none">{item.icon}</span>
          <span>{item.label}</span>
        </NavLink>
      ))}
    </nav>
  )
}
