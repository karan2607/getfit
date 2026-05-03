import { useState, useEffect, useRef, type ReactNode } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthContext, useAuth } from './hooks/useAuth'
import { api, ApiError, type User } from './lib/api'
import { setToken, clearToken, getToken } from './lib/auth'
import { ToastProvider } from './components/Toast'
import Sidebar from './components/Sidebar'
import Login from './pages/Login'
import Signup from './pages/Signup'
import ForgotPassword from './pages/ForgotPassword'
import ResetPassword from './pages/ResetPassword'
import Onboarding from './pages/Onboarding'
import Dashboard from './pages/Dashboard'
import Chat from './pages/Chat'
import Workouts from './pages/Workouts'
import Diet from './pages/Diet'
import Body from './pages/Body'
import Profile from './pages/Profile'

const INACTIVITY_TIMEOUT_MS = 30 * 60 * 1000
const ACTIVITY_EVENTS = ['mousemove', 'keydown', 'click', 'touchstart', 'scroll'] as const

function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const lastActiveRef = useRef(Date.now())

  useEffect(() => {
    if (!getToken()) {
      setIsLoading(false)
      return
    }
    api.auth.me()
      .then(setUser)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) clearToken()
      })
      .finally(() => setIsLoading(false))
  }, [])

  function login(token: string, userData: User) {
    setToken(token)
    setUser(userData)
    lastActiveRef.current = Date.now()
  }

  function logout() {
    api.auth.logout().catch(() => {})
    clearToken()
    setUser(null)
  }

  useEffect(() => {
    if (!user) return

    function onActivity() {
      lastActiveRef.current = Date.now()
    }

    ACTIVITY_EVENTS.forEach((e) => window.addEventListener(e, onActivity, { passive: true }))

    const timer = setInterval(() => {
      if (Date.now() - lastActiveRef.current > INACTIVITY_TIMEOUT_MS) {
        logout()
      }
    }, 60_000)

    return () => {
      ACTIVITY_EVENTS.forEach((e) => window.removeEventListener(e, onActivity))
      clearInterval(timer)
    }
  }, [user]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <AuthContext.Provider value={{ user, isLoading, login, logout, setUser }}>
      {children}
    </AuthContext.Provider>
  )
}

function isProfileComplete(user: User | null): boolean {
  if (!user?.profile) return false
  return !!(user.profile.fitness_goal && user.profile.experience_level && user.profile.dietary_preference)
}

function ProtectedRoute({ children, requireProfile = true }: { children: ReactNode; requireProfile?: boolean }) {
  const { user, isLoading } = useAuth()
  if (isLoading) return <Spinner />
  if (!user) return <Navigate to="/login" replace />
  if (requireProfile && !isProfileComplete(user)) return <Navigate to="/onboarding" replace />
  return <>{children}</>
}

function AppLayout({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  if (!user) return <>{children}</>
  return (
    <div className="flex min-h-screen bg-gray-300">
      <Sidebar />
      <main className="flex-1 min-w-0 bg-stone-50" id="main-content">
        {children}
      </main>
    </div>
  )
}

function Spinner() {
  return (
    <div className="flex items-center justify-center min-h-screen text-gray-400 bg-gray-50">
      <div className="text-center">
        <div className="text-3xl mb-2">💪</div>
        <p className="text-sm">Loading...</p>
      </div>
    </div>
  )
}

function AppRoutes() {
  const { user, isLoading } = useAuth()
  if (isLoading) return <Spinner />

  return (
    <Routes>
      {/* Public auth pages */}
      <Route path="/login" element={user ? <Navigate to="/dashboard" replace /> : <Login />} />
      <Route path="/signup" element={user ? <Navigate to="/dashboard" replace /> : <Signup />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password/:token" element={<ResetPassword />} />

      {/* Onboarding — auth required but profile not required yet */}
      <Route
        path="/onboarding"
        element={
          <ProtectedRoute requireProfile={false}>
            {isProfileComplete(user) ? <Navigate to="/dashboard" replace /> : <Onboarding />}
          </ProtectedRoute>
        }
      />

      {/* Protected app pages */}
      <Route path="/dashboard" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
      <Route path="/chat" element={<ProtectedRoute><Chat /></ProtectedRoute>} />
      <Route path="/chat/:sessionId" element={<ProtectedRoute><Chat /></ProtectedRoute>} />
      <Route path="/workouts" element={<ProtectedRoute><Workouts /></ProtectedRoute>} />
      <Route path="/workouts/session/:sessionId" element={<ProtectedRoute><Workouts /></ProtectedRoute>} />
      <Route path="/workouts/:planId" element={<ProtectedRoute><Workouts /></ProtectedRoute>} />
      <Route path="/diet" element={<ProtectedRoute><Diet /></ProtectedRoute>} />
      <Route path="/body" element={<ProtectedRoute><Body /></ProtectedRoute>} />
      <Route path="/profile" element={<ProtectedRoute requireProfile={false}><Profile /></ProtectedRoute>} />

      {/* Default redirect */}
      <Route path="*" element={<Navigate to={user ? '/dashboard' : '/login'} replace />} />
    </Routes>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
          <AppLayout>
            <AppRoutes />
          </AppLayout>
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  )
}
