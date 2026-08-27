/**
 * Dashboard Page - Overview statistics
 */
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Server, Database, Calendar, TrendingUp, Loader2, AlertCircle } from 'lucide-react';
import api from '../lib/api';
import { DashboardStats } from '../types';
import { formatBytes, getStatusColor } from '../lib/utils';
import { BackupTrendsChart } from '../components/dashboard/BackupTrendsChart';
import { DeviceHealthChart } from '../components/dashboard/DeviceHealthChart';
import { StorageByDeviceChart } from '../components/dashboard/StorageByDeviceChart';

export const Dashboard: React.FC = () => {
  const { data: stats, isLoading, error } = useQuery({
    queryKey: ['dashboard-stats'],
    queryFn: async () => {
      const response = await api.get<DashboardStats>('/statistics/dashboard');
      return response.data;
    },
    refetchInterval: 30000, // Refresh every 30 seconds
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-center">
          <AlertCircle className="h-12 w-12 text-red-500 mx-auto mb-4" />
          <p className="text-gray-600">Failed to load dashboard statistics</p>
        </div>
      </div>
    );
  }

  if (!stats) return null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
        <p className="text-gray-600">Overview of your network backup system</p>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        {/* Devices */}
        <div className="bg-white rounded-lg shadow p-6">
          <div className="flex items-center justify-between mb-4">
            <div className="h-12 w-12 bg-blue-100 rounded-lg flex items-center justify-center">
              <Server className="h-6 w-6 text-blue-600" />
            </div>
            <span className="text-sm font-medium text-green-600">
              {stats.devices.active} active
            </span>
          </div>
          <h3 className="text-2xl font-bold text-gray-900">{stats.devices.total}</h3>
          <p className="text-sm text-gray-600">Total Devices</p>
        </div>

        {/* Backups */}
        <div className="bg-white rounded-lg shadow p-6">
          <div className="flex items-center justify-between mb-4">
            <div className="h-12 w-12 bg-green-100 rounded-lg flex items-center justify-center">
              <Database className="h-6 w-6 text-green-600" />
            </div>
            <span className="text-sm font-medium text-blue-600">
              {stats.backups.success_rate}% success
            </span>
          </div>
          <h3 className="text-2xl font-bold text-gray-900">{stats.backups.total}</h3>
          <p className="text-sm text-gray-600">Total Backups</p>
        </div>

        {/* Jobs */}
        <div className="bg-white rounded-lg shadow p-6">
          <div className="flex items-center justify-between mb-4">
            <div className="h-12 w-12 bg-purple-100 rounded-lg flex items-center justify-center">
              <Calendar className="h-6 w-6 text-purple-600" />
            </div>
            <span className="text-sm font-medium text-purple-600">
              {stats.jobs.enabled} enabled
            </span>
          </div>
          <h3 className="text-2xl font-bold text-gray-900">{stats.jobs.total}</h3>
          <p className="text-sm text-gray-600">Scheduled Jobs</p>
        </div>

        {/* Storage */}
        <div className="bg-white rounded-lg shadow p-6">
          <div className="flex items-center justify-between mb-4">
            <div className="h-12 w-12 bg-orange-100 rounded-lg flex items-center justify-center">
              <TrendingUp className="h-6 w-6 text-orange-600" />
            </div>
            <span className="text-sm font-medium text-gray-600">
              {formatBytes(stats.storage.avg_backup_bytes)} avg
            </span>
          </div>
          <h3 className="text-2xl font-bold text-gray-900">
            {typeof stats.storage.total_gb === 'number' ? stats.storage.total_gb.toFixed(2) : '0.00'} GB
          </h3>
          <p className="text-sm text-gray-600">Total Storage</p>
        </div>
      </div>

      {/* Last 24 Hours */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Last 24 Hours</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <div>
            <p className="text-sm text-gray-600 mb-1">Total Backups</p>
            <p className="text-3xl font-bold text-gray-900">
              {stats.backups.last_24h.total}
            </p>
          </div>
          <div>
            <p className="text-sm text-gray-600 mb-1">Successful</p>
            <p className="text-3xl font-bold text-green-600">
              {stats.backups.last_24h.successful}
            </p>
          </div>
          <div>
            <p className="text-sm text-gray-600 mb-1">Failed</p>
            <p className="text-3xl font-bold text-red-600">
              {stats.backups.last_24h.failed}
            </p>
          </div>
        </div>
      </div>

      {/* Device Types */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Devices by Type</h2>
        <div className="space-y-3">
          {Object.entries(stats.devices.by_type).map(([type, count]) => (
            <div key={type} className="flex items-center justify-between">
              <span className="text-sm text-gray-700 capitalize">
                {type.replace('_', ' ')}
              </span>
              <div className="flex items-center">
                <div className="w-32 h-2 bg-gray-200 rounded-full mr-3">
                  <div
                    className="h-2 bg-blue-600 rounded-full"
                    style={{
                      width: `${(count / stats.devices.total) * 100}%`,
                    }}
                  />
                </div>
                <span className="text-sm font-medium text-gray-900 w-8 text-right">
                  {count}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Charts Section */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Backup Trends */}
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Backup Trends (30 Days)</h2>
          <BackupTrendsChart days={30} />
        </div>

        {/* Device Health */}
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Device Health Distribution</h2>
          <DeviceHealthChart />
        </div>
      </div>

      {/* Storage by Device */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-4">Top 10 Devices by Storage</h2>
        <StorageByDeviceChart />
      </div>

      {/* Recent Activity */}
      <div className="bg-white rounded-lg shadow overflow-hidden">
        <div className="p-6 border-b border-gray-200">
          <h2 className="text-lg font-semibold text-gray-900">Recent Activity</h2>
        </div>
        <div className="divide-y divide-gray-200">
          {stats.recent_activity.items.map((activity) => (
            <div key={activity.config_id} className="p-6 hover:bg-gray-50">
              <div className="flex items-center justify-between">
                <div className="flex-1">
                  <p className="text-sm font-medium text-gray-900">
                    {activity.device_hostname || `Device #${activity.device_id}`}
                  </p>
                  <p className="text-xs text-gray-500 mt-1">
                    {new Date(activity.backed_up_at).toLocaleString()} •{' '}
                    {formatBytes(activity.file_size)} •{' '}
                    {activity.duration.toFixed(2)}s
                  </p>
                </div>
                <span
                  className={`px-2 py-1 text-xs font-medium rounded ${getStatusColor(
                    activity.status
                  )}`}
                >
                  {activity.status}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
