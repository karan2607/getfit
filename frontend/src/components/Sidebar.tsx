import { useState, useEffect } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

interface NavItem {
  to: string
  label: string
  icon: string
}

const NAV_ITEMS: NavItem[] = [
  { to: '/dashboard', label: 'Dashboard', icon: '⊞' },
  { to: '/chat', label: 'AI Trainer', icon: '💬' },
  { to: '/workouts', label: 'Workouts', icon: '🏋️' },
  { to: '/diet', label: 'Diet', icon: '🥗' },
  { to: '/body', label: 'Body Scanner', icon: '📷' },
  { to: '/profile', label: 'Profile', icon: '👤' },
]

export default function Sidebar() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const [isOpen, setIsOpen] = useState(() => {
    const stored = localStorage.getItem('sidebar_open')
    return stored !== null ? stored === 'true' : true
  })
  const [isMobile, setIsMobile] = useState(window.innerWidth < 768)

  useEffect(() => {
    function handleResize() {
      const mobile = window.innerWidth < 768
      setIsMobile(mobile)
      if (mobile) setIsOpen(false)
    }
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  useEffect(() => {
    if (!isMobile) {
      localStorage.setItem('sidebar_open', String(isOpen))
    }
  }, [isOpen, isMobile])

  function handleLogout() {
    logout()
    navigate('/login')
  }

  return (
    <>
      {/* Mobile overlay */}
      {isMobile && isOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-20"
          onClick={() => setIsOpen(false)}
        />
      )}

      {/* Toggle button — always visible */}
      <button
        onClick={() => setIsOpen((v) => !v)}
        className="fixed top-4 left-4 z-30 w-8 h-8 flex flex-col items-center justify-center gap-1.5 rounded-lg bg-gray-900 hover:bg-gray-700 transition-colors"
        aria-label="Toggle sidebar"
      >
        <span className="block w-4 h-0.5 bg-white rounded" />
        <span className="block w-4 h-0.5 bg-white rounded" />
        <span className="block w-4 h-0.5 bg-white rounded" />
      </button>

      {/* Sidebar panel */}
      <aside
        className={`
          fixed top-0 left-0 h-full z-20 bg-gray-900 text-white flex flex-col
          transition-all duration-200 ease-in-out
          ${isMobile
            ? isOpen ? 'w-64 translate-x-0' : 'w-64 -translate-x-full'
            : isOpen ? 'w-64' : 'w-16'
          }
        `}
      >
        {/* Logo */}
        <div className={`flex items-center gap-3 px-4 pt-5 pb-6 ${!isOpen && !isMobile ? 'justify-center px-0' : ''}`}>
          <span className="text-2xl flex-shrink-0">💪</span>
          {(isOpen || isMobile) && (
            <span className="text-lg font-bold tracking-tight">GetFit</span>
          )}
        </div>

        {/* Nav */}
        <nav className="flex-1 px-2 space-y-1 overflow-y-auto">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              onClick={() => isMobile && setIsOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-colors
                 ${isActive
                   ? 'bg-emerald-600 text-white'
                   : 'text-gray-400 hover:bg-gray-800 hover:text-white'
                 }
                 ${!isOpen && !isMobile ? 'justify-center px-0' : ''}
                `
              }
              title={!isOpen && !isMobile ? item.label : undefined}
            >
              <span className="text-lg flex-shrink-0">{item.icon}</span>
              {(isOpen || isMobile) && <span>{item.label}</span>}
            </NavLink>
          ))}
        </nav>

        {/* User + logout */}
        <div className={`p-3 border-t border-gray-800 ${!isOpen && !isMobile ? 'flex justify-center' : ''}`}>
          {(isOpen || isMobile) ? (
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-full bg-emerald-600 flex items-center justify-center text-xs font-bold flex-shrink-0">
                {user?.name?.charAt(0)?.toUpperCase() ?? '?'}
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-white truncate">{user?.name}</p>
                <p className="text-xs text-gray-400 truncate">{user?.email}</p>
              </div>
              <button
                onClick={handleLogout}
                title="Sign out"
                className="text-gray-400 hover:text-white text-lg transition-colors flex-shrink-0"
              >
                ↪
              </button>
            </div>
          ) : (
            <button
              onClick={handleLogout}
              title="Sign out"
              className="text-gray-400 hover:text-white text-lg transition-colors"
            >
              ↪
            </button>
          )}
        </div>
      </aside>
    </>
  )
}
