/**
 * Main App Component
 * Sets up routing, authentication, and global providers
 */
import React, { Suspense, lazy } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Toaster } from 'react-hot-toast';
import { Loader2 } from 'lucide-react';
import { AuthProvider } from './contexts/AuthContext';
import { ProtectedRoute } from './components/ProtectedRoute';
import { MainLayout } from './components/layout/MainLayout';
import { Login } from './pages/auth/Login';

// Routes are split so the initial bundle carries only the shell and the login
// screen. Recharts (dashboard) and the diff viewer (compare) are the two
// largest dependencies in the app and are fetched only when those routes are
// actually visited.
const Register = lazy(() =>
  import('./pages/auth/Register').then((m) => ({ default: m.Register }))
);
const Dashboard = lazy(() =>
  import('./pages/Dashboard').then((m) => ({ default: m.Dashboard }))
);
const Devices = lazy(() =>
  import('./pages/Devices').then((m) => ({ default: m.Devices }))
);
const Backups = lazy(() =>
  import('./pages/Backups').then((m) => ({ default: m.Backups }))
);
const Jobs = lazy(() => import('./pages/Jobs').then((m) => ({ default: m.Jobs })));
const Compare = lazy(() =>
  import('./pages/Compare').then((m) => ({ default: m.Compare }))
);
const Settings = lazy(() =>
  import('./pages/Settings').then((m) => ({ default: m.Settings }))
);
const Discovery = lazy(() =>
  import('./pages/Discovery').then((m) => ({ default: m.Discovery }))
);
const Topology = lazy(() =>
  import('./pages/Topology').then((m) => ({ default: m.Topology }))
);
const Inventory = lazy(() =>
  import('./pages/Inventory').then((m) => ({ default: m.Inventory }))
);
const Reports = lazy(() =>
  import('./pages/Reports').then((m) => ({ default: m.Reports }))
);

// Create React Query client
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 30000, // 30 seconds
      // Drop cached pages a few minutes after the last component using them
      // unmounts, rather than holding every visited page's data for the life
      // of the tab.
      gcTime: 5 * 60 * 1000,
    },
  },
});

const RouteFallback: React.FC = () => (
  <div className="flex items-center justify-center h-64">
    <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
  </div>
);

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <Toaster
            position="top-right"
            toastOptions={{
              duration: 4000,
              style: {
                background: '#363636',
                color: '#fff',
              },
              success: {
                duration: 3000,
                iconTheme: {
                  primary: '#10b981',
                  secondary: '#fff',
                },
              },
              error: {
                duration: 5000,
                iconTheme: {
                  primary: '#ef4444',
                  secondary: '#fff',
                },
              },
            }}
          />

          <Suspense fallback={<RouteFallback />}>
            <Routes>
              {/* Public routes */}
              <Route path="/login" element={<Login />} />
              <Route path="/register" element={<Register />} />

              {/* Protected routes */}
              <Route
                path="/"
                element={
                  <ProtectedRoute>
                    <MainLayout />
                  </ProtectedRoute>
                }
              >
                <Route index element={<Dashboard />} />
                <Route path="devices" element={<Devices />} />
                <Route path="backups" element={<Backups />} />
                <Route
                  path="jobs"
                  element={
                    <ProtectedRoute adminOnly>
                      <Jobs />
                    </ProtectedRoute>
                  }
                />
                <Route path="compare" element={<Compare />} />
                <Route path="discovery" element={<Discovery />} />
                <Route path="topology" element={<Topology />} />
                <Route path="inventory" element={<Inventory />} />
                <Route path="reports" element={<Reports />} />
                <Route path="settings" element={<Settings />} />
              </Route>

              {/* Catch all - redirect to dashboard */}
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

export default App;
