/**
 * Backups Page - Configuration backup management
 */
import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Database,
  Download,
  Search,
  FileText,
  CheckCircle,
  XCircle,
  Clock,
  HardDrive,
  Calendar,
  Eye,
  X
} from 'lucide-react';
import { toast } from 'react-hot-toast';
import api from '../lib/api';
import { Configuration, PaginatedResponse } from '../types';

export const Backups: React.FC = () => {
  const [page, setPage] = useState(1);
  const [limit] = useState(20);
  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [viewingBackup, setViewingBackup] = useState<Configuration | null>(null);
  const [configContent, setConfigContent] = useState<string>('');

  // Fetch backups
  const { data: backupsData, isLoading } = useQuery({
    queryKey: ['backups', page, limit, searchTerm, statusFilter],
    queryFn: async () => {
      const skip = (page - 1) * limit;
      let url = `/backups?skip=${skip}&limit=${limit}`;

      if (searchTerm) {
        url += `&search=${encodeURIComponent(searchTerm)}`;
      }

      if (statusFilter && statusFilter !== 'all') {
        url += `&status=${statusFilter}`;
      }

      const response = await api.get<PaginatedResponse<Configuration>>(url);
      return response.data;
    },
  });

  const handleDownload = async (backup: Configuration) => {
    try {
      const response = await api.get(`/backups/${backup.id}/download`, {
        responseType: 'blob',
      });

      // Create download link
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', backup.filename);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);

      toast.success('Backup downloaded successfully');
    } catch (error: any) {
      toast.error(error.response?.data?.detail || 'Failed to download backup');
    }
  };

  const handleView = async (backup: Configuration) => {
    try {
      setViewingBackup(backup);
      const response = await api.get(`/backups/${backup.id}/download`, {
        responseType: 'text',
      });
      setConfigContent(response.data);
    } catch (error: any) {
      toast.error(error.response?.data?.detail || 'Failed to load configuration');
      setViewingBackup(null);
    }
  };

  const handleCloseViewer = () => {
    setViewingBackup(null);
    setConfigContent('');
  };

  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
  };

  const formatDuration = (seconds: number): string => {
    if (seconds < 60) return `${seconds.toFixed(1)}s`;
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = seconds % 60;
    return `${minutes}m ${remainingSeconds.toFixed(0)}s`;
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'success':
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
            <CheckCircle className="h-3 w-3 mr-1" />
            Success
          </span>
        );
      case 'failed':
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800">
            <XCircle className="h-3 w-3 mr-1" />
            Failed
          </span>
        );
      case 'running':
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
            <Clock className="h-3 w-3 mr-1 animate-spin" />
            Running
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800">
            {status}
          </span>
        );
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Configuration Backups</h1>
          <p className="text-gray-600">View and manage device configuration backups</p>
        </div>
      </div>

      {/* Filters */}
      <div className="bg-white rounded-lg shadow p-4">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {/* Search */}
          <div className="md:col-span-2">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-5 w-5 text-gray-400" />
              <input
                type="text"
                placeholder="Search by hostname or IP address..."
                value={searchTerm}
                onChange={(e) => {
                  setSearchTerm(e.target.value);
                  setPage(1); // Reset to first page on search
                }}
                className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>
          </div>

          {/* Status Filter */}
          <div>
            <select
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value);
                setPage(1);
              }}
              className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            >
              <option value="all">All Statuses</option>
              <option value="success">Success</option>
              <option value="failed">Failed</option>
              <option value="running">Running</option>
            </select>
          </div>
        </div>
      </div>

      {/* Backups List */}
      {isLoading ? (
        <div className="bg-white rounded-lg shadow p-12 text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto"></div>
          <p className="mt-4 text-gray-600">Loading backups...</p>
        </div>
      ) : backupsData && backupsData.items.length > 0 ? (
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Device
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Filename
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Status
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Backup Time
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Size
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Duration
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {backupsData.items.map((backup) => (
                <tr key={backup.id} className="hover:bg-gray-50">
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className="flex items-center">
                      <Database className="h-5 w-5 text-gray-400 mr-3" />
                      <div>
                        <div className="text-sm font-medium text-gray-900">
                          {backup.device_hostname || 'Unknown'}
                        </div>
                        {backup.device_ip && (
                          <div className="text-sm text-gray-500">{backup.device_ip}</div>
                        )}
                      </div>
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex items-center">
                      <FileText className="h-4 w-4 text-gray-400 mr-2" />
                      <span className="text-sm text-gray-900 font-mono">{backup.filename}</span>
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    {getStatusBadge(backup.status)}
                    {backup.error_message && (
                      <div className="text-xs text-red-600 mt-1" title={backup.error_message}>
                        {backup.error_message.substring(0, 50)}
                        {backup.error_message.length > 50 ? '...' : ''}
                      </div>
                    )}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className="flex items-center text-sm text-gray-900">
                      <Calendar className="h-4 w-4 text-gray-400 mr-2" />
                      <div>
                        <div>{new Date(backup.backed_up_at).toLocaleDateString()}</div>
                        <div className="text-xs text-gray-500">
                          {new Date(backup.backed_up_at).toLocaleTimeString()}
                        </div>
                      </div>
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className="flex items-center text-sm text-gray-900">
                      <HardDrive className="h-4 w-4 text-gray-400 mr-2" />
                      {formatFileSize(backup.file_size)}
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                    <div className="flex items-center">
                      <Clock className="h-4 w-4 text-gray-400 mr-2" />
                      {formatDuration(backup.backup_duration)}
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                    {backup.status === 'success' && (
                      <div className="flex items-center justify-end space-x-3">
                        <button
                          onClick={() => handleView(backup)}
                          className="inline-flex items-center text-green-600 hover:text-green-900"
                          title="View Configuration"
                        >
                          <Eye className="h-5 w-5" />
                        </button>
                        <button
                          onClick={() => handleDownload(backup)}
                          className="inline-flex items-center text-blue-600 hover:text-blue-900"
                          title="Download"
                        >
                          <Download className="h-5 w-5" />
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* Pagination */}
          {backupsData.total_pages > 1 && (
            <div className="bg-gray-50 px-4 py-3 flex items-center justify-between border-t border-gray-200">
              <div className="flex-1 flex justify-between sm:hidden">
                <button
                  onClick={() => setPage(page - 1)}
                  disabled={page === 1}
                  className="relative inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50"
                >
                  Previous
                </button>
                <button
                  onClick={() => setPage(page + 1)}
                  disabled={page === backupsData.total_pages}
                  className="ml-3 relative inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50"
                >
                  Next
                </button>
              </div>
              <div className="hidden sm:flex-1 sm:flex sm:items-center sm:justify-between">
                <div>
                  <p className="text-sm text-gray-700">
                    Showing <span className="font-medium">{(page - 1) * limit + 1}</span> to{' '}
                    <span className="font-medium">
                      {Math.min(page * limit, backupsData.total)}
                    </span>{' '}
                    of <span className="font-medium">{backupsData.total}</span> backups
                  </p>
                </div>
                <div>
                  <nav className="relative z-0 inline-flex rounded-md shadow-sm -space-x-px">
                    <button
                      onClick={() => setPage(page - 1)}
                      disabled={page === 1}
                      className="relative inline-flex items-center px-2 py-2 rounded-l-md border border-gray-300 bg-white text-sm font-medium text-gray-500 hover:bg-gray-50 disabled:opacity-50"
                    >
                      Previous
                    </button>
                    {Array.from({ length: Math.min(backupsData.total_pages, 7) }, (_, i) => {
                      let pageNum;
                      if (backupsData.total_pages <= 7) {
                        pageNum = i + 1;
                      } else if (page <= 4) {
                        pageNum = i + 1;
                      } else if (page >= backupsData.total_pages - 3) {
                        pageNum = backupsData.total_pages - 6 + i;
                      } else {
                        pageNum = page - 3 + i;
                      }
                      return (
                        <button
                          key={pageNum}
                          onClick={() => setPage(pageNum)}
                          className={`relative inline-flex items-center px-4 py-2 border text-sm font-medium ${
                            pageNum === page
                              ? 'z-10 bg-blue-50 border-blue-500 text-blue-600'
                              : 'bg-white border-gray-300 text-gray-500 hover:bg-gray-50'
                          }`}
                        >
                          {pageNum}
                        </button>
                      );
                    })}
                    <button
                      onClick={() => setPage(page + 1)}
                      disabled={page === backupsData.total_pages}
                      className="relative inline-flex items-center px-2 py-2 rounded-r-md border border-gray-300 bg-white text-sm font-medium text-gray-500 hover:bg-gray-50 disabled:opacity-50"
                    >
                      Next
                    </button>
                  </nav>
                </div>
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="bg-white rounded-lg shadow p-12 text-center">
          <Database className="h-16 w-16 text-gray-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">No backups found</h3>
          <p className="text-gray-600">
            {searchTerm || statusFilter !== 'all'
              ? 'Try adjusting your filters'
              : 'Backups will appear here once you trigger a backup for your devices'}
          </p>
        </div>
      )}

      {/* Configuration Viewer Modal */}
      {viewingBackup && (
        <div className="fixed inset-0 z-50 overflow-y-auto">
          <div className="flex items-center justify-center min-h-screen px-4 pt-4 pb-20 text-center sm:block sm:p-0">
            {/* Background overlay */}
            <div
              className="fixed inset-0 transition-opacity bg-gray-500 bg-opacity-75"
              onClick={handleCloseViewer}
            />

            {/* Modal panel */}
            <div className="inline-block align-bottom bg-white rounded-lg text-left overflow-hidden shadow-xl transform transition-all sm:my-8 sm:align-middle sm:max-w-6xl sm:w-full">
              {/* Header */}
              <div className="bg-gray-50 px-6 py-4 border-b flex items-center justify-between">
                <div className="flex items-center">
                  <FileText className="h-6 w-6 text-blue-600 mr-3" />
                  <div>
                    <h3 className="text-lg font-semibold text-gray-900">
                      {viewingBackup.device_hostname || 'Configuration'}
                    </h3>
                    <p className="text-sm text-gray-600">{viewingBackup.filename}</p>
                  </div>
                </div>
                <div className="flex items-center space-x-3">
                  <button
                    onClick={() => handleDownload(viewingBackup)}
                    className="inline-flex items-center px-3 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50 transition"
                    title="Download"
                  >
                    <Download className="h-4 w-4 mr-2" />
                    Download
                  </button>
                  <button
                    onClick={handleCloseViewer}
                    className="text-gray-400 hover:text-gray-600 transition"
                  >
                    <X className="h-6 w-6" />
                  </button>
                </div>
              </div>

              {/* Content */}
              <div className="bg-white px-6 py-4" style={{ maxHeight: '70vh', overflowY: 'auto' }}>
                <pre className="text-sm font-mono text-gray-800 whitespace-pre-wrap break-words bg-gray-50 p-4 rounded-lg border border-gray-200">
                  {configContent || 'Loading...'}
                </pre>
              </div>

              {/* Footer */}
              <div className="bg-gray-50 px-6 py-4 border-t flex items-center justify-between text-sm text-gray-600">
                <div className="flex items-center space-x-6">
                  <span>
                    <HardDrive className="h-4 w-4 inline mr-1" />
                    {formatFileSize(viewingBackup.file_size)}
                  </span>
                  <span>
                    <Calendar className="h-4 w-4 inline mr-1" />
                    {new Date(viewingBackup.backed_up_at).toLocaleString()}
                  </span>
                  <span>
                    <Clock className="h-4 w-4 inline mr-1" />
                    {formatDuration(viewingBackup.backup_duration)}
                  </span>
                </div>
                <button
                  onClick={handleCloseViewer}
                  className="px-4 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-100 transition"
                >
                  Close
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
