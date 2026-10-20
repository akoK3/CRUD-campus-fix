import { NavLink, Navigate, Route, Routes, Link, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'
import { useAuth } from './auth/AuthProvider'
import { BrandMark, Loading } from './components/ui'
import LoginPage from './pages/LoginPage'
import CoverPage from './pages/CoverPage'
import SubmitTicketPage from './pages/SubmitTicketPage'
import MyTicketsPage from './pages/MyTicketsPage'
import TicketDetailPage from './pages/TicketDetailPage'
import StaffQueuePage from './pages/StaffQueuePage'
import AnalyticsPage from './pages/AnalyticsPage'
import CampusMapPage from './pages/CampusMapPage'
import ResetPasswordPage from './pages/ResetPasswordPage'
import NotificationsPage from './pages/NotificationsPage'
import ProfilePage from './pages/ProfilePage'
import UsersPage from './pages/UsersPage'
import NotificationBell from './components/NotificationBell'

function RequireAuth({ children }: { children: ReactNode }) {
  const { session, loading } = useAuth()
  if (loading) return <Loading />
  if (!session) return <Navigate to="/login" replace />
  return <>{children}</>
}

function RequireStaff({ children }: { children: ReactNode }) {
  const { session, loading, isStaff } = useAuth()
  if (loading) return <Loading />
  if (!session) return <Navigate to="/login" replace />
  // Cosmetic only. The database refuses the rows regardless of this check.
  if (!isStaff) return <Navigate to="/tickets" replace />
  return <>{children}</>
}

function RequireAdmin({ children }: { children: ReactNode }) {
  const { session, loading, isAdmin } = useAuth()
  if (loading) return <Loading />
  if (!session) return <Navigate to="/login" replace />
  if (!isAdmin) return <Navigate to="/tickets" replace />
  return <>{children}</>
}

function Chrome({ children }: { children: ReactNode }) {
  const { profile, isStaff, isAdmin, isSystemAdmin, signOut } = useAuth()

  return (
    <div className="app">
      <header className="site-header">
        <div className="topbar">
          <Link to="/tickets" className="brand">
            <BrandMark size={34} />
            <span>
              CampusFix
              <small>Caldwell University</small>
            </span>
          </Link>
          <NotificationBell />
          {profile && (
            <Link to="/profile" className="who" title="Your profile">
              {profile.first_name} {profile.last_name}
              <br />
              <span className="badge">{profile.role.replace(/_/g, ' ')}</span>
            </Link>
          )}
          <button className="sm" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
        <nav className="nav">
          <NavLink to="/submit">Report an issue</NavLink>
          <NavLink to="/tickets">My tickets</NavLink>
          {isStaff && <NavLink to="/queue">Queue</NavLink>}
          {isStaff && <NavLink to="/map">Map</NavLink>}
          {isStaff && <NavLink to="/analytics">Analytics</NavLink>}
          {isAdmin && <NavLink to="/admin/users">{isSystemAdmin ? 'Users' : 'Staff'}</NavLink>}
        </nav>
      </header>
      {children}
    </div>
  )
}

export default function App() {
  const { session, loading } = useAuth()
  const { pathname } = useLocation()

  if (loading) return <Loading />

  // Reachable signed in or not: the reset link itself creates the session,
  // and the page explains an expired link when it doesn't.
  if (pathname === '/reset-password') return <ResetPasswordPage />

  if (!session) {
    return (
      <Routes>
        <Route path="/" element={<CoverPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    )
  }

  return (
    <Chrome>
      <Routes>
        <Route path="/login" element={<Navigate to="/tickets" replace />} />
        <Route
          path="/submit"
          element={
            <RequireAuth>
              <SubmitTicketPage />
            </RequireAuth>
          }
        />
        <Route
          path="/tickets"
          element={
            <RequireAuth>
              <MyTicketsPage />
            </RequireAuth>
          }
        />
        <Route
          path="/tickets/:id"
          element={
            <RequireAuth>
              <TicketDetailPage />
            </RequireAuth>
          }
        />
        <Route
          path="/queue"
          element={
            <RequireStaff>
              <StaffQueuePage />
            </RequireStaff>
          }
        />
        <Route
          path="/map"
          element={
            <RequireStaff>
              <CampusMapPage />
            </RequireStaff>
          }
        />
        <Route
          path="/analytics"
          element={
            <RequireStaff>
              <AnalyticsPage />
            </RequireStaff>
          }
        />
        <Route
          path="/notifications"
          element={
            <RequireAuth>
              <NotificationsPage />
            </RequireAuth>
          }
        />
        <Route
          path="/profile"
          element={
            <RequireAuth>
              <ProfilePage />
            </RequireAuth>
          }
        />
        <Route
          path="/admin/users"
          element={
            <RequireAdmin>
              <UsersPage />
            </RequireAdmin>
          }
        />
        <Route path="*" element={<Navigate to="/tickets" replace />} />
      </Routes>
    </Chrome>
  )
}
