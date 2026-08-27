/**
 * Main Layout Component
 * Provides the main application layout with sidebar navigation
 */
import React, { useState } from 'react';
import { Link, Outlet, useLocation } from 'react-router-dom';
import {
  LayoutDashboard,
  Server,
  Database,
  Calendar,
  GitCompare,
  LogOut,
  Menu,
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
          'fixed inset-y-0 left-0 z-30 w-64 bg-gray-900 transform transition-transform duration-300 ease-in-out lg:translate-x-0',
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        )}
      >
        <div className="flex flex-col h-full">
          {/* Logo */}
          <div className="flex items-center justify-between h-16 px-4 bg-gray-800">
            <div className="flex items-center">
              <Database className="h-8 w-8 text-blue-500" />
              <span className="ml-2 text-white font-semibold text-lg">
                BlackHawk NetConfig
              </span>
            </div>
            <button
              className="lg:hidden text-gray-400 hover:text-white"
              onClick={() => setSidebarOpen(false)}
            >
              <X className="h-6 w-6" />
            </button>
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
                  className={cn(
                    'flex items-center px-4 py-3 text-sm font-medium rounded-lg transition-colors',
                    isActive
                      ? 'bg-gray-800 text-white'
                      : 'text-gray-300 hover:bg-gray-800 hover:text-white'
                  )}
                >
                  <Icon className="h-5 w-5 mr-3" />
                  {item.name}
                </Link>
              );
            })}
          </nav>

          {/* User section */}
          <div className="border-t border-gray-800 p-4">
            <div className="flex items-center mb-3">
              <div className="flex-shrink-0">
                <div className="h-10 w-10 rounded-full bg-gray-700 flex items-center justify-center">
                  <User className="h-6 w-6 text-gray-300" />
                </div>
              </div>
              <div className="ml-3 flex-1 min-w-0">
                <p className="text-sm font-medium text-white truncate">
                  {user?.username}
                </p>
                <p className="text-xs text-gray-400 truncate">{user?.email}</p>
              </div>
            </div>

            <button
              onClick={logout}
              className="w-full flex items-center px-4 py-2 text-sm font-medium text-gray-300 hover:bg-gray-800 hover:text-white rounded-lg transition-colors"
            >
              <LogOut className="h-5 w-5 mr-3" />
              Logout
            </button>
          </div>
        </div>
      </div>

      {/* Main content */}
      <div className="lg:pl-64">
        {/* Top bar */}
        <div className="sticky top-0 z-10 bg-white border-b border-gray-200 h-16 flex items-center px-4 lg:px-8">
          <button
            className="lg:hidden text-gray-500 hover:text-gray-700"
            onClick={() => setSidebarOpen(true)}
          >
            <Menu className="h-6 w-6" />
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
