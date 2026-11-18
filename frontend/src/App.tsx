import { lazy, Suspense } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { LoaderCircle } from 'lucide-react'
import { AuthProvider } from './components/AuthProvider'
import { ErrorBoundary } from './components/ErrorBoundary'
import { LiveFeedProvider } from './components/LiveFeedProvider'
import { ToastProvider } from './components/ToastProvider'
import { RequireAuth, RequireRole } from './components/AuthGuard'

const AppShell = lazy(() => import('./layouts/AppShell'))
const LoginPage = lazy(() => import('./pages/LoginPage'))
const OverviewPage = lazy(() => import('./pages/OverviewPage'))
const NewInspectionPage = lazy(() => import('./pages/NewInspectionPage'))
const HistoryPage = lazy(() => import('./pages/HistoryPage'))
const InspectionDetailPage = lazy(() => import('./pages/InspectionDetailPage'))
const CamerasPage = lazy(() => import('./pages/CamerasPage'))
const RecipesPage = lazy(() => import('./pages/RecipesPage'))
const BatchesPage = lazy(() => import('./pages/BatchesPage'))
const ModelsPage = lazy(() => import('./pages/ModelsPage'))
const AdminPage = lazy(() => import('./pages/AdminPage'))
const MaintenancePage = lazy(() => import('./pages/MaintenancePage'))
const KnowledgePage = lazy(() => import('./pages/KnowledgePage'))
const AgentPage = lazy(() => import('./pages/AgentPage'))
const MonitoringPage = lazy(() => import('./pages/MonitoringPage'))
const LabPage = lazy(() => import('./pages/LabPage'))
const NotFoundPage = lazy(() => import('./pages/NotFoundPage'))

function RouteFallback() {
  return <div className="boot-screen" role="status"><LoaderCircle className="spin" size={25} /><p>Membuka ruang kerja…</p></div>
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <LiveFeedProvider>
        <ToastProvider>
          <ErrorBoundary>
          <Suspense fallback={<RouteFallback />}>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route element={<RequireAuth />}>
                <Route element={<AppShell />}>
                  <Route index element={<OverviewPage />} />
                  <Route path="/inspections" element={<HistoryPage />} />
                  <Route path="/inspections/new" element={<NewInspectionPage />} />
                  <Route path="/inspections/:id" element={<InspectionDetailPage />} />
                  <Route path="/cameras" element={<CamerasPage />} />
                  <Route path="/recipes" element={<RecipesPage />} />
                  <Route path="/batches" element={<BatchesPage />} />
                  <Route path="/models" element={<ModelsPage />} />
                  <Route path="/maintenance" element={<MaintenancePage />} />
                  <Route path="/knowledge" element={<KnowledgePage />} />
                  <Route path="/agent" element={<AgentPage />} />
                  <Route path="/monitoring" element={<MonitoringPage />} />
                  <Route path="/lab" element={<LabPage />} />
                  <Route element={<RequireRole roles={['admin']} />}>
                    <Route path="/admin" element={<AdminPage />} />
                  </Route>
                  <Route path="*" element={<NotFoundPage />} />
                </Route>
              </Route>
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
          </ErrorBoundary>
        </ToastProvider>
        </LiveFeedProvider>
      </AuthProvider>
    </BrowserRouter>
  )
}
