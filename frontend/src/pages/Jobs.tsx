/**
 * Jobs Page - Scheduled backup job management
 */
import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Calendar,
  Plus,
  Edit,
  Trash2,
  Power,
  PowerOff,
  Clock,
  PlayCircle,
  CheckCircle,
  XCircle
} from 'lucide-react';
import { toast } from 'react-hot-toast';
import api from '../lib/api';
import { BackupJob, BackupJobCreate, BackupJobUpdate, PaginatedResponse } from '../types';

export const Jobs: React.FC = () => {
  const [showAddModal, setShowAddModal] = useState(false);
  const [editingJob, setEditingJob] = useState<BackupJob | null>(null);
  const [page, setPage] = useState(1);
  const [limit] = useState(20);
  const queryClient = useQueryClient();

  // Fetch jobs
  const { data: jobsData, isLoading } = useQuery({
    queryKey: ['backup-jobs', page, limit],
    queryFn: async () => {
      const skip = (page - 1) * limit;
      const response = await api.get<PaginatedResponse<BackupJob>>(`/backup-jobs?skip=${skip}&limit=${limit}`);
      return response.data;
    },
  });

  // Delete job mutation
  const deleteMutation = useMutation({
    mutationFn: async (jobId: number) => {
      await api.delete(`/backup-jobs/${jobId}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['backup-jobs'] });
      toast.success('Job deleted successfully');
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.detail || 'Failed to delete job');
    },
  });

  // Toggle job mutation
  const toggleMutation = useMutation({
    mutationFn: async ({ jobId, isEnabled }: { jobId: number; isEnabled: boolean }) => {
      await api.patch(`/backup-jobs/${jobId}`, { is_enabled: isEnabled });
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['backup-jobs'] });
      toast.success(`Job ${variables.isEnabled ? 'enabled' : 'disabled'} successfully`);
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.detail || 'Failed to update job');
    },
  });

  // Trigger job mutation
  const triggerMutation = useMutation({
    mutationFn: async (jobId: number) => {
      const response = await api.post(`/backup-jobs/${jobId}/run`);
      return response.data;
    },
    onSuccess: () => {
      toast.success('Job triggered successfully');
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.detail || 'Failed to trigger job');
    },
  });

  const handleDelete = (job: BackupJob) => {
    if (window.confirm(`Are you sure you want to delete job "${job.name}"?`)) {
      deleteMutation.mutate(job.id);
    }
  };

  const handleToggle = (job: BackupJob) => {
    toggleMutation.mutate({ jobId: job.id, isEnabled: !job.is_enabled });
  };

  const handleTrigger = (job: BackupJob) => {
    triggerMutation.mutate(job.id);
  };

  const getStatusBadge = (job: BackupJob) => {
    if (!job.is_enabled) {
      return (
        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800">
          <PowerOff className="h-3 w-3 mr-1" />
          Disabled
        </span>
      );
    }

    return (
      <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
        <Power className="h-3 w-3 mr-1" />
        Enabled
      </span>
    );
  };

  const formatNextRun = (nextRun: string | null | undefined) => {
    if (!nextRun) return 'Not scheduled';
    const date = new Date(nextRun);
    const now = new Date();
    const diff = date.getTime() - now.getTime();

    if (diff < 0) return 'Overdue';

    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(minutes / 60);
    const days = Math.floor(hours / 24);

    if (days > 0) return `in ${days} day${days > 1 ? 's' : ''}`;
    if (hours > 0) return `in ${hours} hour${hours > 1 ? 's' : ''}`;
    return `in ${minutes} minute${minutes !== 1 ? 's' : ''}`;
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Scheduled Backup Jobs</h1>
          <p className="text-gray-600">Automate backups with cron schedules</p>
        </div>
        <button
          onClick={() => setShowAddModal(true)}
          className="inline-flex items-center px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition"
        >
          <Plus className="h-5 w-5 mr-2" />
          Add Job
        </button>
      </div>

      {/* Jobs List */}
      {isLoading ? (
        <div className="bg-white rounded-lg shadow p-12 text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto"></div>
          <p className="mt-4 text-gray-600">Loading jobs...</p>
        </div>
      ) : jobsData && jobsData.items.length > 0 ? (
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Job
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Schedule
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Status
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Last Run
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Next Run
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {jobsData.items.map((job) => (
                <tr key={job.id} className="hover:bg-gray-50">
                  <td className="px-6 py-4">
                    <div className="flex items-center">
                      <Calendar className="h-5 w-5 text-gray-400 mr-3" />
                      <div>
                        <div className="text-sm font-medium text-gray-900">{job.name}</div>
                        {job.description && (
                          <div className="text-sm text-gray-500">{job.description}</div>
                        )}
                      </div>
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <code className="text-sm bg-gray-100 px-2 py-1 rounded font-mono">
                      {job.schedule_cron}
                    </code>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    {getStatusBadge(job)}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                    {job.last_run_at
                      ? new Date(job.last_run_at).toLocaleString()
                      : 'Never'}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className="flex items-center text-sm text-gray-900">
                      <Clock className="h-4 w-4 text-gray-400 mr-2" />
                      {job.is_enabled ? formatNextRun(job.next_run_at) : 'Disabled'}
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                    <div className="flex justify-end gap-2">
                      <button
                        onClick={() => handleTrigger(job)}
                        className="text-green-600 hover:text-green-900"
                        title="Run Now"
                        disabled={!job.is_enabled}
                      >
                        <PlayCircle className="h-5 w-5" />
                      </button>
                      <button
                        onClick={() => handleToggle(job)}
                        className={job.is_enabled ? 'text-orange-600 hover:text-orange-900' : 'text-blue-600 hover:text-blue-900'}
                        title={job.is_enabled ? 'Disable' : 'Enable'}
                      >
                        {job.is_enabled ? <PowerOff className="h-5 w-5" /> : <Power className="h-5 w-5" />}
                      </button>
                      <button
                        onClick={() => setEditingJob(job)}
                        className="text-blue-600 hover:text-blue-900"
                        title="Edit"
                      >
                        <Edit className="h-5 w-5" />
                      </button>
                      <button
                        onClick={() => handleDelete(job)}
                        className="text-red-600 hover:text-red-900"
                        title="Delete"
                      >
                        <Trash2 className="h-5 w-5" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* Pagination */}
          {jobsData.total_pages > 1 && (
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
                  disabled={page === jobsData.total_pages}
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
                      {Math.min(page * limit, jobsData.total)}
                    </span>{' '}
                    of <span className="font-medium">{jobsData.total}</span> jobs
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
                    {Array.from({ length: jobsData.total_pages }, (_, i) => i + 1).map((p) => (
                      <button
                        key={p}
                        onClick={() => setPage(p)}
                        className={`relative inline-flex items-center px-4 py-2 border text-sm font-medium ${
                          p === page
                            ? 'z-10 bg-blue-50 border-blue-500 text-blue-600'
                            : 'bg-white border-gray-300 text-gray-500 hover:bg-gray-50'
                        }`}
                      >
                        {p}
                      </button>
                    ))}
                    <button
                      onClick={() => setPage(page + 1)}
                      disabled={page === jobsData.total_pages}
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
          <Calendar className="h-16 w-16 text-gray-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">No scheduled jobs yet</h3>
          <p className="text-gray-600 mb-4">
            Create automated backup schedules using cron syntax.
          </p>
          <button
            onClick={() => setShowAddModal(true)}
            className="inline-flex items-center px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition"
          >
            <Plus className="h-5 w-5 mr-2" />
            Add Job
          </button>
        </div>
      )}

      {/* Add/Edit Job Modal */}
      {(showAddModal || editingJob) && (
        <JobModal
          job={editingJob}
          onClose={() => {
            setShowAddModal(false);
            setEditingJob(null);
          }}
          onSuccess={() => {
            setShowAddModal(false);
            setEditingJob(null);
            queryClient.invalidateQueries({ queryKey: ['backup-jobs'] });
          }}
        />
      )}
    </div>
  );
};

// Job Add/Edit Modal Component
interface JobModalProps {
  job?: BackupJob | null;
  onClose: () => void;
  onSuccess: () => void;
}

const JobModal: React.FC<JobModalProps> = ({ job, onClose, onSuccess }) => {
  const [formData, setFormData] = useState<BackupJobCreate>({
    name: job?.name || '',
    description: job?.description || '',
    schedule_cron: job?.schedule_cron || '0 2 * * *',
    is_enabled: job?.is_enabled ?? true,
    device_filter: job?.device_filter || {},
  });

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (job) {
        // Update existing job
        const updateData: BackupJobUpdate = { ...formData };
        await api.put(`/backup-jobs/${job.id}`, updateData);
      } else {
        // Create new job
        await api.post('/backup-jobs', formData);
      }
    },
    onSuccess: () => {
      toast.success(job ? 'Job updated successfully' : 'Job added successfully');
      onSuccess();
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.detail || 'Failed to save job');
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    saveMutation.mutate();
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const { name, value } = e.target;
    setFormData((prev) => ({
      ...prev,
      [name]: value,
    }));
  };

  const commonCronPresets = [
    { label: 'Every day at 2 AM', value: '0 2 * * *' },
    { label: 'Every day at midnight', value: '0 0 * * *' },
    { label: 'Every 6 hours', value: '0 */6 * * *' },
    { label: 'Every hour', value: '0 * * * *' },
    { label: 'Every Monday at 2 AM', value: '0 2 * * 1' },
    { label: 'First day of month at 2 AM', value: '0 2 1 * *' },
  ];

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
        <div className="p-6">
          <h2 className="text-2xl font-bold text-gray-900 mb-6">
            {job ? 'Edit Job' : 'Add Scheduled Job'}
          </h2>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Job Name *
              </label>
              <input
                type="text"
                name="name"
                required
                value={formData.name}
                onChange={handleChange}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                placeholder="Daily backup"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Description
              </label>
              <textarea
                name="description"
                value={formData.description}
                onChange={handleChange}
                rows={2}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                placeholder="Backup all active devices daily"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Cron Schedule *
              </label>
              <input
                type="text"
                name="schedule_cron"
                required
                value={formData.schedule_cron}
                onChange={handleChange}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent font-mono"
                placeholder="0 2 * * *"
              />
              <p className="text-xs text-gray-500 mt-1">
                Format: minute hour day month weekday
              </p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Common Schedules
              </label>
              <div className="grid grid-cols-2 gap-2">
                {commonCronPresets.map((preset) => (
                  <button
                    key={preset.value}
                    type="button"
                    onClick={() => setFormData((prev) => ({ ...prev, schedule_cron: preset.value }))}
                    className="text-left px-3 py-2 border border-gray-300 rounded-lg hover:bg-blue-50 hover:border-blue-500 text-sm transition"
                  >
                    <div className="font-medium text-gray-900">{preset.label}</div>
                    <div className="text-xs text-gray-500 font-mono">{preset.value}</div>
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="flex items-center">
                <input
                  type="checkbox"
                  name="is_enabled"
                  checked={formData.is_enabled}
                  onChange={(e) =>
                    setFormData((prev) => ({ ...prev, is_enabled: e.target.checked }))
                  }
                  className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                />
                <span className="ml-2 text-sm text-gray-700">Enable this job</span>
              </label>
            </div>

            <div className="flex justify-end gap-3 mt-6 pt-6 border-t">
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50 transition"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={saveMutation.isPending}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition disabled:opacity-50"
              >
                {saveMutation.isPending ? 'Saving...' : job ? 'Update Job' : 'Add Job'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
};
