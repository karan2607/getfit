import { useState, useEffect, useRef, type ComponentType } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import {
  HomeIcon, ChatIcon, WorkoutsIcon, SessionsIcon, DietIcon, HealthIcon,
  ScannerIcon, ProfileIcon, LogoMark,
} from './NavIcons'

interface NavItem {
  to: string
  label: string
  /*
   * A component, not a string.
   *
   * These were emoji rendered as text, which is why the sidebar ignored the
   * active colour entirely: emoji are font glyphs drawn in the platform's own
   * multi-colour bitmap, so `currentColor` does not reach them. The bottom
   * nav was moved to drawn icons and this was left behind, so the two navs
   * disagreed about what the app looked like.
   */
  Icon: ComponentType<{ className?: string }>
}

const NAV_ITEMS: NavItem[] = [
  { to: '/dashboard', label: 'Dashboard', Icon: HomeIcon },
  { to: '/chat', label: 'AI Trainer', Icon: ChatIcon },
  { to: '/workouts', label: 'Workouts', Icon: WorkoutsIcon },
  // Existed as a route and was in neither nav: reachable only by
  // guessing the URL.
  { to: '/sessions', label: 'Session History', Icon: SessionsIcon },
  { to: '/diet', label: 'Diet', Icon: DietIcon },
  { to: '/health', label: 'Health Sync', Icon: HealthIcon },
  { to: '/body', label: 'Body Scanner', Icon: ScannerIcon },
  { to: '/profile', label: 'Profile', Icon: ProfileIcon },
]

export default function Sidebar() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const [isOpen, setIsOpen] = useState(false)
  const [isMobile, setIsMobile] = useState(window.innerWidth < 768)
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    function handleResize() {
      const mobile = window.innerWidth < 768
      setIsMobile(mobile)
      if (mobile) setIsOpen(false)
    }
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  function handleMouseEnter() {
    if (closeTimer.current) clearTimeout(closeTimer.current)
    setIsOpen(true)
  }

  function handleMouseLeave() {
    closeTimer.current = setTimeout(() => setIsOpen(false), 150)
  }

  function handleLogout() {
    logout()
    navigate('/login')
  }

  const sidebarContent = (
    <div className="flex flex-col h-full bg-ink-600 text-white border-r border-ink-500">
      {/* Logo */}
      <div className="flex items-center gap-3 px-4 pt-5 pb-6 overflow-hidden">
        <LogoMark className="flex-shrink-0 text-accent-500" />
        {isOpen && (
          <span className="text-lg font-bold tracking-tight whitespace-nowrap text-white">GetFit</span>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 px-2 space-y-1 overflow-y-auto overflow-x-hidden">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            onClick={() => isMobile && setIsOpen(false)}
            className={({ isActive }) =>
              /*
               * `text-accent-500` on the whole row, matching BottomNav, so
               * the icon and the label change together. The old treatment
               * was a filled `bg-brand-500` pill, which the icon could not
               * participate in while it was an emoji -- the two navs
               * signalled "active" in two different ways for that reason.
               */
              `flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-semibold transition-colors overflow-hidden
               ${isActive ? 'bg-ink-500 text-accent-500' : 'text-ink-100 hover:bg-ink-400 hover:text-white'}
               ${!isOpen ? 'justify-center px-0' : ''}
              `
            }
            title={!isOpen ? item.label : undefined}
          >
            <item.Icon className="flex-shrink-0" />
            {isOpen && <span className="whitespace-nowrap">{item.label}</span>}
          </NavLink>
        ))}
      </nav>

      {/* User + logout */}
      <div className={`p-3 border-t border-ink-500 overflow-hidden ${!isOpen ? 'flex justify-center' : ''}`}>
        {isOpen ? (
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-full bg-brand-500 flex items-center justify-center text-xs font-bold text-white flex-shrink-0">
              {user?.name?.charAt(0)?.toUpperCase() ?? '?'}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-white truncate">{user?.name}</p>
              <p className="text-xs text-gray-300 truncate">{user?.email}</p>
            </div>
            <button onClick={handleLogout} title="Sign out" className="text-gray-300 hover:text-white text-lg transition-colors flex-shrink-0">
              ↪
            </button>
          </div>
        ) : (
          <button onClick={handleLogout} title="Sign out" className="text-gray-300 hover:text-white text-lg transition-colors">
            ↪
          </button>
        )}
      </div>
    </div>
  )

  if (isMobile) return null

  return (
    <aside
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      className={`sticky top-0 h-screen flex-shrink-0 transition-all duration-200 ${isOpen ? 'w-64' : 'w-16'}`}
    >
      {sidebarContent}
    </aside>
  )
}
