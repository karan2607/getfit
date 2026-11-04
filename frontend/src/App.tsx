import { useState, useEffect, useRef, type ReactNode } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthContext, useAuth } from './hooks/useAuth'
import { api, ApiError, type User } from './lib/api'
import { setToken, clearToken, getToken } from './lib/auth'
import { ToastProvider } from './components/Toast'
import Sidebar from './components/Sidebar'
import BottomNav from './components/BottomNav'
import FeedbackButton from './components/FeedbackButton'
import Login from './pages/Login'
import Signup from './pages/Signup'
import ForgotPassword from './pages/ForgotPassword'
import ResetPassword from './pages/ResetPassword'
import Onboarding from './pages/Onboarding'
import Dashboard from './pages/Dashboard'
import Chat from './pages/Chat'
import Workouts from './pages/Workouts'
import Sessions from './pages/Sessions'
import Diet from './pages/Diet'
import Body from './pages/Body'
import Profile from './pages/Profile'
import Health from './pages/Health'
import FeedbackThread from './pages/FeedbackThread'

const INACTIVITY_TIMEOUT_MS = 30 * 60 * 1000
const ACTIVITY_EVENTS = ['mousemove', 'keydown', 'click', 'touchstart', 'scroll'] as const

function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  // Derived from whether a token exists, rather than defaulting to `true` and
  // immediately setting it false in an effect. The effect version renders a
  // loading screen for one frame on every signed-out visit, and is a
  // synchronous setState inside an effect -- the cascading-render pattern.
  const [isLoading, setIsLoading] = useState(() => Boolean(getToken()))
  // Not Date.now(): calling it during render is impure. It is written by the
  // inactivity effect before anything reads it.
  const lastActiveRef = useRef(0)

  useEffect(() => {
    if (!getToken()) return
    api.auth.me()
      .then(setUser)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) clearToken()
      })
      .finally(() => setIsLoading(false))
  }, [])

  /**
   * Report the browser's timezone so the server can work out the user's own
   * "today".
   *
   * The server is UTC and had no way to know the local day: meals logged at
   * 11pm on the 19th were stored under the 19th (the client sends the local
   * date) and then read back against the 20th, so the health page showed 0
   * eaten. Sent once per session, and only when it has actually changed.
   */
  useEffect(() => {
    if (!user) return
    let detected: string
    try {
      detected = Intl.DateTimeFormat().resolvedOptions().timeZone || ''
    } catch {
      return
    }
    if (!detected || detected === user.profile?.timezone) return
    api.profile.update({ timezone: detected })
      .then((profile) => setUser({ ...user, profile }))
      .catch(() => {})   // never block the app on this
  }, [user?.id, user?.profile?.timezone])

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

    // Seeded here rather than at render, so the clock starts when the
    // listeners do.
    lastActiveRef.current = Date.now()

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
    <div className="flex h-dvh overflow-hidden bg-ink-600">
      <Sidebar />
      <main className="flex-1 min-w-0 bg-stone-50 overflow-y-auto overflow-x-hidden pb-16 md:pb-0 flex flex-col" id="main-content">
        {children}
      </main>
      <BottomNav />
      {/* Renders nothing unless the signed-in user is a superuser. */}
      <FeedbackButton />
    </div>
  )
}

/**
 * The first screen of every session.
 *
 * "Loading..." said nothing about a wait that can genuinely run to ~45
 * seconds: the backend is on Render's free tier and cold-starts, so a bare
 * word on a blank screen reads as a frozen app. People close frozen apps.
 *
 * The copy escalates with the actual elapsed time rather than guessing in
 * advance, so a fast load stays quiet and a slow one explains itself.
 */
function Spinner() {
  const [seconds, setSeconds] = useState(0)

  useEffect(() => {
    const started = Date.now()
    const id = setInterval(
      () => setSeconds(Math.floor((Date.now() - started) / 1000)),
      1000,
    )
    return () => clearInterval(id)
  }, [])

  return (
    <div className="flex items-center justify-center min-h-screen text-gray-500 bg-gray-50 px-6">
      <div className="text-center max-w-xs">
        <div className="text-3xl mb-3 animate-pulse">💪</div>
        <p className="text-sm font-medium text-ink-600">
          {seconds < 6 ? 'Loading…' : 'Waking up the server'}
        </p>
        {seconds >= 6 && (
          <p className="text-xs text-gray-500 mt-2">
            This can take up to ~45 seconds on the free tier. {seconds}s
          </p>
        )}
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
      <Route path="/sessions" element={<ProtectedRoute><Sessions /></ProtectedRoute>} />
      <Route path="/diet" element={<ProtectedRoute><Diet /></ProtectedRoute>} />
      <Route path="/body" element={<ProtectedRoute><Body /></ProtectedRoute>} />
      <Route path="/health" element={<ProtectedRoute><Health /></ProtectedRoute>} />
      <Route path="/profile" element={<ProtectedRoute requireProfile={false}><Profile /></ProtectedRoute>} />
      {/* Superuser-only in practice: the page renders nothing for anyone
          else and every endpoint behind it returns 403. */}
      <Route path="/feedback/:id" element={<ProtectedRoute requireProfile={false}><FeedbackThread /></ProtectedRoute>} />

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
