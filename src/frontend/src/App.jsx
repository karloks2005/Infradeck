import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useEffect, useState } from 'react'
import AccountPage from './account/AccountPage.jsx'
import AdminPage from './admin/AdminPage.jsx'
import MorePage from './more/MorePage.jsx'
import AgentPage from './agent/AgentPage.jsx'
import { apiRequest } from './api.js'
import { LoginPage, RegisterPage } from './auth/AuthPages.jsx'
import DashboardPage from './dashboard/DashboardPage.jsx'
import { resetOverview } from './overviewStore.js'
import { applyDisplay } from './prefs.js'
import StatisticsPage from './statistics/StatisticsPage.jsx'

const AUTH_KEY = 'homie-auth-user'

function getStoredUser() {
  try {
    const raw = window.localStorage.getItem(AUTH_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function RequireAuth({ user, children }) {
  const location = useLocation()

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  // Someone who still has to set up two-factor can only use the account page.
  if (user.setup_required && location.pathname !== '/account') {
    return <Navigate to="/account" replace />
  }

  return children
}

function App() {
  const [user, setUser] = useState(() => getStoredUser())

  useEffect(() => {
    try {
      if (user) window.localStorage.setItem(AUTH_KEY, JSON.stringify(user))
      else window.localStorage.removeItem(AUTH_KEY)
    } catch {
      // Private windows: the sign-in just isn't remembered across reloads.
    }
  }, [user])

  useEffect(() => {
    if (!user) return
    apiRequest('/auth/me', { method: 'GET' })
      .then((data) => setUser((current) => ({ ...(current || {}), ...data.user })))
      .catch(() => setUser(null))
    // Only on load: this checks that the remembered sign-in is still valid.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Theme and density from this browser at once, then from the account once it has loaded.
  useEffect(() => { applyDisplay() }, [])
  useEffect(() => {
    if (!user?.id || user.setup_required) return
    apiRequest('/account', { method: 'GET' }).then((account) => applyDisplay(account.preferences.display)).catch(() => {})
  }, [user?.id, user?.setup_required])

  const handleLogout = async () => {
    try {
      await apiRequest('/auth/logout', { method: 'POST' })
    } catch {
      // Clear local state even if the session has already expired.
    }
    resetOverview()
    setUser(null)
  }
  const page = (element) => <RequireAuth user={user}>{element}</RequireAuth>

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={user ? <Navigate to="/" replace /> : <LoginPage onLogin={setUser} />} />
        <Route path="/register" element={user ? <Navigate to="/" replace /> : <RegisterPage onRegister={setUser} />} />
        <Route path="/" element={page(<DashboardPage user={user} onLogout={handleLogout} />)} />
        <Route path="/dashboard" element={page(<Navigate to="/" replace />)} />
        <Route path="/statistics" element={page(<StatisticsPage user={user} onLogout={handleLogout} />)} />
        <Route path="/agent" element={page(<AgentPage user={user} onLogout={handleLogout} />)} />
        <Route path="/reports" element={page(<Navigate to="/agent" replace />)} />
        <Route path="/logs" element={page(<Navigate to="/agent" replace />)} />
        <Route path="/more" element={page(<MorePage user={user} onLogout={handleLogout} />)} />
        <Route path="/account" element={page(<AccountPage user={user} onLogout={handleLogout} onUserChange={(patch) => setUser((current) => (current ? { ...current, ...patch } : current))} />)} />
        <Route path="/admin" element={page(user?.role === 'admin' ? <AdminPage user={user} onLogout={handleLogout} /> : <Navigate to="/" replace />)} />
      </Routes>
    </BrowserRouter>
  )
}

export default App
