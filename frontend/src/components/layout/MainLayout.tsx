/**
 * Main Layout Component
 * Provides the main application layout with sidebar navigation
 *
 * The sidebar has two independent states. On a small screen it slides in over
 * the page and is dismissed by the overlay; on a large one it is always
 * present and can be collapsed to a rail of icons, which is remembered per
 * browser. They are separate because they answer different questions - "show
 * me the menu" and "give the page back its 12rem" - and a phone has no room
 * for the second.
 */
import React, { useEffect, useState } from 'react';
import { Link, Outlet, useLocation } from 'react-router-dom';
import {
  LayoutDashboard,
  Server,
  Database,
  Calendar,
  GitCompare,
  LogOut,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  X,
  User,
  Settings,
  Radar,
  Network,
  Boxes,
  BarChart3,
} from 'lucide-react';
import { useAuth } from '../../contexts/AuthContext';
import { usePermissions } from '../../hooks/usePermissions';
import { cn } from '../../lib/utils';

interface NavItem {
  name: string;
  path: string;
  icon: React.ElementType;
  adminOnly?: boolean;
  permission?: string;
}

const COLLAPSED_KEY = 'sidebar-collapsed';

/** Remembered per browser, so the choice survives a reload */
function useCollapsed(): [boolean, (next: boolean) => void] {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem(COLLAPSED_KEY) === 'true';
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      window.localStorage.setItem(COLLAPSED_KEY, String(collapsed));
    } catch {
      // Not being able to remember it is no reason to ignore it.
    }
  }, [collapsed]);

  return [collapsed, setCollapsed];
}

const navigation: NavItem[] = [
  { name: 'Dashboard', path: '/', icon: LayoutDashboard },
  { name: 'Devices', path: '/devices', icon: Server },
  { name: 'Backups', path: '/backups', icon: Database },
  { name: 'Scheduled Jobs', path: '/jobs', icon: Calendar, adminOnly: true },
  { name: 'Compare', path: '/compare', icon: GitCompare },
  { name: 'Discovery', path: '/discovery', icon: Radar, permission: 'discovery:read' },
  { name: 'Topology', path: '/topology', icon: Network, permission: 'discovery:read' },
  { name: 'Inventory', path: '/inventory', icon: Boxes, permission: 'inventory:read' },
  { name: 'Reports', path: '/reports', icon: BarChart3, permission: 'reports:read' },
  { name: 'Settings', path: '/settings', icon: Settings },
];

export const MainLayout: React.FC = () => {
  const { user, logout } = useAuth();
  const { can, isLoading: permissionsLoading } = usePermissions();
  const location = useLocation();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [collapsed, setCollapsed] = useCollapsed();

  // The rail is a desktop idea. Sliding the sidebar in over a phone screen
  // and then showing icons only would be the worst of both.
  const railed = collapsed && !sidebarOpen;

  const filteredNavigation = navigation.filter((item) => {
    if (item.adminOnly && !user?.is_admin) return false;
    // While the permission list is still in flight, fall back to the legacy
    // admin flag rather than flashing an empty sidebar.
    if (item.permission) {
      return permissionsLoading ? Boolean(user?.is_admin) : can(item.permission);
    }
    return true;
  });

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Mobile sidebar overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-gray-600 bg-opacity-75 z-20 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <div
        className={cn(
          'fixed inset-y-0 left-0 z-30 bg-gray-900 transform transition-all duration-300 ease-in-out lg:translate-x-0',
          railed ? 'w-16' : 'w-64',
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        )}
      >
        <div className="flex flex-col h-full">
          {/* Logo */}
          <div
            className={cn(
              'flex items-center h-16 bg-gray-800',
              railed ? 'justify-center px-2' : 'justify-between px-4'
            )}
          >
            <Link
              to="/"
              className="flex items-center min-w-0"
              title={railed ? 'BlackHawk NetConfig' : undefined}
            >
              <Database className="h-8 w-8 text-blue-500 shrink-0" />
              {!railed && (
                // Not truncated: the name is wider than the sidebar at this
                // size and has always wrapped to two lines, which reads
                // better than "BlackHawk NetC...".
                <span className="ml-2 text-white font-semibold text-lg leading-tight">
                  BlackHawk NetConfig
                </span>
              )}
            </Link>
            {!railed && (
              <button
                className="lg:hidden text-gray-400 hover:text-white"
                onClick={() => setSidebarOpen(false)}
                aria-label="Close menu"
              >
                <X className="h-6 w-6" />
              </button>
            )}
          </div>

          {/* Navigation */}
          <nav className="flex-1 px-2 py-4 space-y-1 overflow-y-auto">
            {filteredNavigation.map((item) => {
              const Icon = item.icon;
              const isActive = location.pathname === item.path;

              return (
                <Link
                  key={item.path}
                  to={item.path}
                  onClick={() => setSidebarOpen(false)}
                  // The name is the only label there is once collapsed, so it
                  // becomes the tooltip rather than disappearing entirely.
                  title={railed ? item.name : undefined}
                  className={cn(
                    'flex items-center py-3 text-sm font-medium rounded-lg transition-colors',
                    railed ? 'justify-center px-2' : 'px-4',
                    isActive
                      ? 'bg-gray-800 text-white'
                      : 'text-gray-300 hover:bg-gray-800 hover:text-white'
                  )}
                >
                  <Icon className="h-5 w-5 shrink-0" />
                  {!railed && <span className="ml-3 truncate">{item.name}</span>}
                </Link>
              );
            })}
          </nav>

          {/* User section */}
          <div className={cn('border-t border-gray-800', railed ? 'p-2' : 'p-4')}>
            <div
              className={cn(
                'flex items-center mb-3',
                railed && 'justify-center'
              )}
              title={railed ? user?.username : undefined}
            >
              <div className="flex-shrink-0">
                <div
                  className={cn(
                    'rounded-full bg-gray-700 flex items-center justify-center',
                    railed ? 'h-9 w-9' : 'h-10 w-10'
                  )}
                >
                  <User className="h-6 w-6 text-gray-300" />
                </div>
              </div>
              {!railed && (
                <div className="ml-3 flex-1 min-w-0">
                  <p className="text-sm font-medium text-white truncate">
                    {user?.username}
                  </p>
                  <p className="text-xs text-gray-400 truncate">{user?.email}</p>
                </div>
              )}
            </div>

            <button
              onClick={logout}
              title={railed ? 'Logout' : undefined}
              className={cn(
                'w-full flex items-center py-2 text-sm font-medium text-gray-300 hover:bg-gray-800 hover:text-white rounded-lg transition-colors',
                railed ? 'justify-center px-2' : 'px-4'
              )}
            >
              <LogOut className="h-5 w-5 shrink-0" />
              {!railed && <span className="ml-3">Logout</span>}
            </button>
          </div>
        </div>
      </div>

      {/* Main content */}
      <div className={cn('transition-all duration-300', collapsed ? 'lg:pl-16' : 'lg:pl-64')}>
        {/* Top bar */}
        <div className="sticky top-0 z-10 bg-white border-b border-gray-200 h-16 flex items-center px-4 lg:px-8">
          <button
            className="lg:hidden text-gray-500 hover:text-gray-700"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open menu"
          >
            <Menu className="h-6 w-6" />
          </button>

          <button
            className="hidden lg:inline-flex text-gray-500 hover:text-gray-700 -ml-2 mr-4"
            onClick={() => setCollapsed(!collapsed)}
            data-testid="sidebar-toggle"
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            aria-expanded={!collapsed}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            {collapsed ? (
              <PanelLeftOpen className="h-6 w-6" />
            ) : (
              <PanelLeftClose className="h-6 w-6" />
            )}
          </button>

          <div className="flex-1 flex items-center justify-between lg:justify-end">
            <h1 className="text-xl font-semibold text-gray-900 ml-4 lg:ml-0">
              {navigation.find((item) => item.path === location.pathname)?.name ||
                'BlackHawk NetConfig'}
            </h1>

            <div className="flex items-center space-x-4">
              {user?.is_admin && (
                <span className="px-2 py-1 text-xs font-medium bg-blue-100 text-blue-800 rounded">
                  Admin
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Page content */}
        <main className="p-4 lg:p-8">
          <Outlet />
        </main>

        {/* Footer */}
        <footer className="bg-white border-t border-gray-200 mt-8">
          <div className="px-4 lg:px-8 py-6">
            <div className="flex flex-col md:flex-row justify-between items-center text-sm text-gray-600">
              <div className="mb-4 md:mb-0">
                <p className="font-semibold text-gray-900">BlackHawk NetConfig</p>
                <p>Professional Network Configuration Management</p>
              </div>
              <div className="flex flex-col md:flex-row items-center space-y-2 md:space-y-0 md:space-x-6">
                <a
                  href="https://blackhawk11.com"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:text-blue-600 transition"
                >
                  blackhawk11.com
                </a>
                <a
                  href="mailto:info@blackhawk11.com"
                  className="hover:text-blue-600 transition"
                >
                  info@blackhawk11.com
                </a>
                <span className="text-gray-400">© {new Date().getFullYear()} BlackHawk Data</span>
              </div>
            </div>
          </div>
        </footer>
      </div>
    </div>
  );
};
