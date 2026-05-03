import { useState, useEffect, useRef } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

interface NavItem {
  to: string
  label: string
  icon: string
}

const NAV_ITEMS: NavItem[] = [
  { to: '/dashboard', label: 'Dashboard', icon: '🏠' },
  { to: '/chat', label: 'AI Trainer', icon: '💬' },
  { to: '/workouts', label: 'Workouts', icon: '🏋️' },
  { to: '/diet', label: 'Diet', icon: '🥗' },
  { to: '/body', label: 'Body Scanner', icon: '📷' },
  { to: '/profile', label: 'Profile', icon: '👤' },
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
    <div className="flex flex-col h-full bg-gray-300 text-gray-800 border-r border-gray-200">
      {/* Logo */}
      <div className="flex items-center gap-3 px-4 pt-5 pb-6 overflow-hidden">
        <span className="text-2xl flex-shrink-0">💪</span>
        {isOpen && (
          <span className="text-lg font-bold tracking-tight whitespace-nowrap text-gray-900">GetFit</span>
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
              `flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-colors overflow-hidden
               ${isActive ? 'bg-brand-500 text-white' : 'text-gray-500 hover:bg-gray-300 hover:text-gray-900'}
               ${!isOpen ? 'justify-center px-0' : ''}
              `
            }
            title={!isOpen ? item.label : undefined}
          >
            <span className="text-lg flex-shrink-0">{item.icon}</span>
            {isOpen && <span className="whitespace-nowrap">{item.label}</span>}
          </NavLink>
        ))}
      </nav>

      {/* User + logout */}
      <div className={`p-3 border-t border-gray-200 overflow-hidden ${!isOpen ? 'flex justify-center' : ''}`}>
        {isOpen ? (
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-full bg-brand-500 flex items-center justify-center text-xs font-bold text-white flex-shrink-0">
              {user?.name?.charAt(0)?.toUpperCase() ?? '?'}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-gray-900 truncate">{user?.name}</p>
              <p className="text-xs text-gray-500 truncate">{user?.email}</p>
            </div>
            <button onClick={handleLogout} title="Sign out" className="text-gray-400 hover:text-gray-700 text-lg transition-colors flex-shrink-0">
              ↪
            </button>
          </div>
        ) : (
          <button onClick={handleLogout} title="Sign out" className="text-gray-400 hover:text-gray-700 text-lg transition-colors">
            ↪
          </button>
        )}
      </div>
    </div>
  )

  if (isMobile) {
    return (
      <>
        {isOpen && <div className="fixed inset-0 z-20 bg-black/40" onClick={() => setIsOpen(false)} />}
        <aside
          onClick={() => !isOpen && setIsOpen(true)}
          className={`fixed top-0 left-0 h-full z-30 transition-all duration-200 ${isOpen ? 'w-64' : 'w-16'}`}
        >
          {sidebarContent}
        </aside>
      </>
    )
  }

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
