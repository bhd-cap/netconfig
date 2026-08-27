/**
 * Devices Page - Network device management
 *
 * The list is sortable on every column the API catalogues, rows can be
 * selected in bulk to change details or drop them from the backup list, and
 * the hostname drills into everything discovery learned about the device.
 */
import React, { useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Server,
  Plus,
  Edit,
  Trash2,
  Power,
  PowerOff,
  RefreshCw,
  Activity,
  CheckCircle,
  XCircle,
  ArrowUp,
  ArrowDown,
  ArrowUpDown,
  Search,
  ShieldCheck,
  ShieldX,
  ShieldQuestion,
  X,
} from 'lucide-react';
import { toast } from 'react-hot-toast';
import api from '../lib/api';
import { usePermissions } from '../hooks/usePermissions';
import { DeviceDetailPanel } from '../components/devices/DeviceDetailPanel';
import {
  AuthStatus,
  BulkDeviceUpdate,
  Device,
  DeviceCreate,
  DeviceUpdate,
  PaginatedResponse,
  DEVICE_TYPES,
  TRANSPORTS,
  Transport,
} from '../types';

type SortDir = 'asc' | 'desc';

/**
 * The columns with a header the user can click
 *
 * Every key here is in the API's own SORTABLE_COLUMNS catalogue; sorting on
 * anything else is refused with a 400 rather than silently ignored.
 */
const SORTABLE: Array<{ key: string; label: string }> = [
  { key: 'hostname', label: 'Device' },
  { key: 'ip_address', label: 'IP Address' },
  { key: 'device_type', label: 'Type' },
  { key: 'transport', label: 'Transport' },
  { key: 'last_auth_status', label: 'Login' },
  { key: 'is_active', label: 'Status' },
  { key: 'last_backup_at', label: 'Last Backup' },
];

const AUTH_LABELS: Record<AuthStatus, string> = {
  never: 'Not tried',
  success: 'Authenticated',
  auth_failed: 'Login refused',
  unreachable: 'No answer',
  error: 'Probe error',
};

/**
 * Whether a login has ever succeeded, at a glance
 *
 * A device with no working credential can still be crawled and inventoried,
 * so this is deliberately separate from the active/inactive flag.
 */
const AuthBadge: React.FC<{ device: Device }> = ({ device }) => {
  const status = (device.last_auth_status ?? 'never') as AuthStatus;

  const tone =
    status === 'success'
      ? 'bg-emerald-100 text-emerald-800'
      : status === 'never'
      ? 'bg-gray-100 text-gray-700'
      : 'bg-amber-100 text-amber-800';

  const Icon =
    status === 'success' ? ShieldCheck : status === 'never' ? ShieldQuestion : ShieldX;

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${tone}`}
      title={device.auth_error ?? AUTH_LABELS[status]}
    >
      <Icon className="h-3 w-3 mr-1" />
      {AUTH_LABELS[status] ?? status}
    </span>
  );
};

export const Devices: React.FC = () => {
  const [showAddModal, setShowAddModal] = useState(false);
  const [editingDevice, setEditingDevice] = useState<Device | null>(null);
  const [detailDeviceId, setDetailDeviceId] = useState<number | null>(null);
  const [bulkEditing, setBulkEditing] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState('hostname');
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [page, setPage] = useState(1);
  const [limit] = useState(20);
  const queryClient = useQueryClient();
  const { can } = usePermissions();
  const canWrite = can('devices:write');
  const canDelete = can('devices:delete');

  // Fetch devices
  const { data: devicesData, isLoading } = useQuery({
    queryKey: ['devices', page, limit, sortBy, sortDir, search],
    queryFn: async () => {
      const params = new URLSearchParams({
        skip: String((page - 1) * limit),
        limit: String(limit),
        sort_by: sortBy,
        sort_dir: sortDir,
      });
      if (search.trim()) params.set('search', search.trim());
      const response = await api.get<PaginatedResponse<Device>>(`/devices?${params}`);
      return response.data;
    },
  });

  const rows = devicesData?.items ?? [];

  // Only what is on screen can be selected, so the header checkbox and the
  // bulk actions both mean "these rows" and never a hidden page.
  const selectedOnPage = useMemo(
    () => rows.filter((device) => selected.has(device.id)),
    [rows, selected]
  );
  const allOnPageSelected = rows.length > 0 && selectedOnPage.length === rows.length;

  const clearSelection = () => setSelected(new Set());

  const toggleOne = (deviceId: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(deviceId)) next.delete(deviceId);
      else next.add(deviceId);
      return next;
    });
  };

  const toggleAllOnPage = () => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allOnPageSelected) rows.forEach((device) => next.delete(device.id));
      else rows.forEach((device) => next.add(device.id));
      return next;
    });
  };

  const sort = (key: string) => {
    if (key === sortBy) {
      setSortDir((dir) => (dir === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortBy(key);
      setSortDir('asc');
    }
    setPage(1);
  };

  // Delete device mutation
  const deleteMutation = useMutation({
    mutationFn: async (deviceId: number) => {
      await api.delete(`/devices/${deviceId}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['devices'] });
      toast.success('Device deleted successfully');
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.detail || 'Failed to delete device');
    },
  });

  const bulkUpdateMutation = useMutation({
    mutationFn: async (payload: BulkDeviceUpdate) => {
      const response = await api.patch('/devices/bulk', payload);
      return response.data;
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['devices'] });
      toast.success(data.message || 'Devices updated');
      clearSelection();
      setBulkEditing(false);
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.detail || 'Failed to update devices');
    },
  });

  const bulkDeleteMutation = useMutation({
    mutationFn: async (deviceIds: number[]) => {
      const response = await api.post('/devices/bulk-delete', { device_ids: deviceIds });
      return response.data;
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['devices'] });
      // The inventory keeps its rows, so refresh it too.
      queryClient.invalidateQueries({ queryKey: ['inventory'] });
      toast.success(data.message || 'Devices removed');
      clearSelection();
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.detail || 'Failed to remove devices');
    },
  });

  // Trigger backup mutation
  const backupMutation = useMutation({
    mutationFn: async (deviceIds: number[]) => {
      const response = await api.post(`/backups/trigger`, { device_ids: deviceIds });
      return response.data;
    },
    onSuccess: () => {
      toast.success('Backup triggered successfully');
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.detail || 'Failed to trigger backup');
    },
  });

  // Test connectivity mutation
  const testMutation = useMutation({
    mutationFn: async (deviceId: number) => {
      const response = await api.post(`/devices/${deviceId}/test`);
      return response.data;
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['devices'] });
      if (data.success) {
        toast.success(`Connection successful: ${data.message}`);
      } else {
        toast.error(`Connection failed: ${data.message}`);
      }
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.detail || 'Failed to test connection');
    },
  });

  const handleDelete = (device: Device) => {
    if (window.confirm(`Are you sure you want to delete ${device.hostname}?`)) {
      deleteMutation.mutate(device.id);
    }
  };

  const handleBulkDelete = () => {
    const ids = selectedOnPage.map((device) => device.id);
    if (!ids.length) return;

    const message =
      `Remove ${ids.length} device(s) from the backup list?\n\n` +
      'Their stored configurations go with them, but the hosts seen on their ' +
      'ports and the adjacencies they reported stay in the inventory.';

    if (window.confirm(message)) bulkDeleteMutation.mutate(ids);
  };

  const handleBulkActive = (isActive: boolean) => {
    const ids = selectedOnPage.map((device) => device.id);
    if (!ids.length) return;
    bulkUpdateMutation.mutate({ device_ids: ids, is_active: isActive });
  };

  const getStatusBadge = (device: Device) => {
    if (!device.is_active) {
      return (
        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-800">
          <PowerOff className="h-3 w-3 mr-1" />
          Inactive
        </span>
      );
    }

    if (device.last_backup_status === 'success') {
      return (
        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
          <CheckCircle className="h-3 w-3 mr-1" />
          Healthy
        </span>
      );
    }

    if (device.last_backup_status === 'failed') {
      return (
        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800">
          <XCircle className="h-3 w-3 mr-1" />
          Failed
        </span>
      );
    }

    return (
      <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
        <Power className="h-3 w-3 mr-1" />
        Active
      </span>
    );
  };

  const SortHeader: React.FC<{ column: { key: string; label: string } }> = ({ column }) => {
    const active = sortBy === column.key;
    const Icon = !active ? ArrowUpDown : sortDir === 'asc' ? ArrowUp : ArrowDown;

    return (
      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
        <button
          type="button"
          onClick={() => sort(column.key)}
          data-testid={`sort-${column.key}`}
          aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
          className={`inline-flex items-center gap-1 hover:text-gray-900 transition ${
            active ? 'text-gray-900' : ''
          }`}
          title={`Sort by ${column.label}`}
        >
          {column.label}
          <Icon className={`h-3 w-3 ${active ? 'text-blue-600' : 'text-gray-400'}`} />
        </button>
      </th>
    );
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Devices</h1>
          <p className="text-gray-600">Manage your network devices</p>
        </div>
        <button
          onClick={() => setShowAddModal(true)}
          className="inline-flex items-center px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition"
        >
          <Plus className="h-5 w-5 mr-2" />
          Add Device
        </button>
      </div>

      {/* Search */}
      <div className="relative max-w-md">
        <Search className="h-4 w-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
        <input
          type="search"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
          placeholder="Search hostname or IP"
          className="w-full pl-9 pr-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        />
      </div>

      {/* Bulk action bar */}
      {selectedOnPage.length > 0 && (
        <div
          data-testid="bulk-bar"
          className="flex flex-wrap items-center gap-3 bg-blue-50 border border-blue-200 rounded-lg px-4 py-3"
        >
          <span className="text-sm font-medium text-blue-900">
            {selectedOnPage.length} selected
          </span>

          {canWrite && (
            <>
              <button
                onClick={() => setBulkEditing(true)}
                className="inline-flex items-center px-3 py-1.5 text-sm bg-white border border-blue-300 text-blue-700 rounded hover:bg-blue-100"
              >
                <Edit className="h-4 w-4 mr-1.5" />
                Edit details
              </button>
              <button
                onClick={() => handleBulkActive(true)}
                disabled={bulkUpdateMutation.isPending}
                className="inline-flex items-center px-3 py-1.5 text-sm bg-white border border-blue-300 text-blue-700 rounded hover:bg-blue-100 disabled:opacity-50"
              >
                <Power className="h-4 w-4 mr-1.5" />
                Add to backup list
              </button>
              <button
                onClick={() => handleBulkActive(false)}
                disabled={bulkUpdateMutation.isPending}
                className="inline-flex items-center px-3 py-1.5 text-sm bg-white border border-blue-300 text-blue-700 rounded hover:bg-blue-100 disabled:opacity-50"
              >
                <PowerOff className="h-4 w-4 mr-1.5" />
                Remove from backup list
              </button>
            </>
          )}

          {canDelete && (
            <button
              onClick={handleBulkDelete}
              disabled={bulkDeleteMutation.isPending}
              className="inline-flex items-center px-3 py-1.5 text-sm bg-white border border-red-300 text-red-700 rounded hover:bg-red-50 disabled:opacity-50"
            >
              <Trash2 className="h-4 w-4 mr-1.5" />
              Delete devices
            </button>
          )}

          <button
            onClick={clearSelection}
            className="ml-auto inline-flex items-center text-sm text-blue-700 hover:text-blue-900"
          >
            <X className="h-4 w-4 mr-1" />
            Clear
          </button>
        </div>
      )}

      {/* Devices List */}
      {isLoading ? (
        <div className="bg-white rounded-lg shadow p-12 text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto"></div>
          <p className="mt-4 text-gray-600">Loading devices...</p>
        </div>
      ) : devicesData && devicesData.items.length > 0 ? (
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 w-10">
                    <input
                      type="checkbox"
                      checked={allOnPageSelected}
                      onChange={toggleAllOnPage}
                      data-testid="select-all"
                      aria-label="Select every device on this page"
                      className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                    />
                  </th>
                  {SORTABLE.map((column) => (
                    <SortHeader key={column.key} column={column} />
                  ))}
                  <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {devicesData.items.map((device) => (
                  <tr
                    key={device.id}
                    data-device-id={device.id}
                    className={selected.has(device.id) ? 'bg-blue-50' : 'hover:bg-gray-50'}
                  >
                    <td className="px-4 py-4">
                      <input
                        type="checkbox"
                        checked={selected.has(device.id)}
                        onChange={() => toggleOne(device.id)}
                        aria-label={`Select ${device.hostname}`}
                        className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                      />
                    </td>
                    <td className="px-4 py-4 whitespace-nowrap">
                      <div className="flex items-center">
                        <Server className="h-5 w-5 text-gray-400 mr-3 shrink-0" />
                        <div>
                          <button
                            type="button"
                            onClick={() => setDetailDeviceId(device.id)}
                            data-testid={`device-name-${device.id}`}
                            className="text-sm font-medium text-blue-600 hover:text-blue-800 hover:underline text-left"
                            title="Show everything known about this device"
                          >
                            {device.hostname}
                          </button>
                          <div className="text-sm text-gray-500">
                            {device.model ? device.model : `User: ${device.username}`}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-4 whitespace-nowrap text-sm text-gray-900">
                      {device.ip_address}
                    </td>
                    <td className="px-4 py-4 whitespace-nowrap text-sm text-gray-900">
                      {DEVICE_TYPES[device.device_type as keyof typeof DEVICE_TYPES] ||
                        device.device_type}
                    </td>
                    <td className="px-4 py-4 whitespace-nowrap text-sm text-gray-500">
                      {TRANSPORTS[(device.transport ?? 'ssh') as Transport] ??
                        device.transport}
                    </td>
                    <td className="px-4 py-4 whitespace-nowrap">
                      <AuthBadge device={device} />
                    </td>
                    <td className="px-4 py-4 whitespace-nowrap">{getStatusBadge(device)}</td>
                    <td className="px-4 py-4 whitespace-nowrap text-sm text-gray-500">
                      {device.last_backup_at
                        ? new Date(device.last_backup_at).toLocaleString()
                        : 'Never'}
                    </td>
                    <td className="px-4 py-4 whitespace-nowrap text-right text-sm font-medium">
                      <div className="flex justify-end gap-2">
                        <button
                          onClick={() => testMutation.mutate(device.id)}
                          className="text-purple-600 hover:text-purple-900"
                          title="Test Connection"
                        >
                          <Activity className="h-5 w-5" />
                        </button>
                        <button
                          onClick={() => backupMutation.mutate([device.id])}
                          className="text-green-600 hover:text-green-900"
                          title="Backup Now"
                        >
                          <RefreshCw className="h-5 w-5" />
                        </button>
                        <button
                          onClick={() => setEditingDevice(device)}
                          className="text-blue-600 hover:text-blue-900"
                          title="Edit"
                        >
                          <Edit className="h-5 w-5" />
                        </button>
                        <button
                          onClick={() => handleDelete(device)}
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
          </div>

          {/* Pagination */}
          {devicesData.total_pages > 1 && (
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
                  disabled={page === devicesData.total_pages}
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
                      {Math.min(page * limit, devicesData.total)}
                    </span>{' '}
                    of <span className="font-medium">{devicesData.total}</span> results
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
                    {Array.from({ length: devicesData.total_pages }, (_, i) => i + 1).map((p) => (
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
                      disabled={page === devicesData.total_pages}
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
          <Server className="h-16 w-16 text-gray-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">
            {search ? 'No devices match that search' : 'No devices yet'}
          </h3>
          <p className="text-gray-600 mb-4">
            {search
              ? 'Try a different hostname or IP address.'
              : 'Get started by adding your first network device.'}
          </p>
          <button
            onClick={() => setShowAddModal(true)}
            className="inline-flex items-center px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition"
          >
            <Plus className="h-5 w-5 mr-2" />
            Add Device
          </button>
        </div>
      )}

      {/* Add/Edit Device Modal */}
      {(showAddModal || editingDevice) && (
        <DeviceModal
          device={editingDevice}
          onClose={() => {
            setShowAddModal(false);
            setEditingDevice(null);
          }}
          onSuccess={() => {
            setShowAddModal(false);
            setEditingDevice(null);
            queryClient.invalidateQueries({ queryKey: ['devices'] });
          }}
        />
      )}

      {/* Bulk edit modal */}
      {bulkEditing && (
        <BulkEditModal
          devices={selectedOnPage}
          isSaving={bulkUpdateMutation.isPending}
          onClose={() => setBulkEditing(false)}
          onSubmit={(changes) =>
            bulkUpdateMutation.mutate({
              device_ids: selectedOnPage.map((device) => device.id),
              ...changes,
            })
          }
        />
      )}

      {/* Detail drill-in */}
      {detailDeviceId !== null && (
        <DeviceDetailPanel
          deviceId={detailDeviceId}
          onClose={() => setDetailDeviceId(null)}
        />
      )}
    </div>
  );
};

// Bulk edit modal
interface BulkEditModalProps {
  devices: Device[];
  isSaving: boolean;
  onClose: () => void;
  onSubmit: (changes: Omit<BulkDeviceUpdate, 'device_ids'>) => void;
}

/**
 * Change the same details on a selection
 *
 * Every field starts blank and a blank field is left alone, so a bulk edit
 * only ever writes what was deliberately filled in. Credentials are absent by
 * design - pushing one password onto a selection is how a rack ends up locked
 * out, and the credential vault is where shared logins belong.
 */
const BulkEditModal: React.FC<BulkEditModalProps> = ({
  devices,
  isSaving,
  onClose,
  onSubmit,
}) => {
  const [deviceType, setDeviceType] = useState('');
  const [transport, setTransport] = useState('');
  const [port, setPort] = useState('');
  const [location, setLocation] = useState('');
  const [description, setDescription] = useState('');

  const changes: Omit<BulkDeviceUpdate, 'device_ids'> = {};
  if (deviceType) changes.device_type = deviceType;
  if (transport) changes.transport = transport as Transport;
  if (port) changes.port = parseInt(port, 10);
  if (location) changes.location = location;
  if (description) changes.description = description;

  const nothingToDo = Object.keys(changes).length === 0;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (nothingToDo) return;
    onSubmit(changes);
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl max-w-lg w-full">
        <div className="p-6">
          <h2 className="text-xl font-bold text-gray-900">
            Edit {devices.length} device(s)
          </h2>
          <p className="text-sm text-gray-600 mt-1">
            Anything left blank is kept as it is. Credentials are set per device
            or shared through the credential vault, not here.
          </p>

          <form onSubmit={handleSubmit} className="space-y-4 mt-5">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Device type
              </label>
              <select
                value={deviceType}
                onChange={(e) => setDeviceType(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
              >
                <option value="">Leave unchanged</option>
                {Object.entries(DEVICE_TYPES).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Transport
                </label>
                <select
                  value={transport}
                  onChange={(e) => setTransport(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                >
                  <option value="">Leave unchanged</option>
                  {Object.entries(TRANSPORTS).map(([key, label]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Port
                </label>
                <input
                  type="number"
                  min={1}
                  max={65535}
                  value={port}
                  onChange={(e) => setPort(e.target.value)}
                  placeholder="Leave unchanged"
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
                />
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Location
              </label>
              <input
                type="text"
                value={location}
                onChange={(e) => setLocation(e.target.value)}
                placeholder="Leave unchanged"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Description
              </label>
              <input
                type="text"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Leave unchanged"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500"
              />
            </div>

            <div className="flex justify-end gap-3 pt-4 border-t">
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-2 border border-gray-300 rounded-lg text-gray-700 hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={isSaving || nothingToDo}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
              >
                {isSaving ? 'Saving...' : 'Apply to selection'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
};

// Device Add/Edit Modal Component
interface DeviceModalProps {
  device?: Device | null;
  onClose: () => void;
  onSuccess: () => void;
}

const DeviceModal: React.FC<DeviceModalProps> = ({ device, onClose, onSuccess }) => {
  const [formData, setFormData] = useState<DeviceCreate>({
    hostname: device?.hostname || '',
    ip_address: device?.ip_address || '',
    device_type: device?.device_type || 'cisco_ios',
    username: device?.username || '',
    password: '',
    port: device?.port || 22,
    enable_secret: device?.enable_secret || '',
    is_active: device?.is_active ?? true,
    transport: device?.transport || 'ssh',
    snmp_version: device?.snmp_version || null,
    snmp_port: device?.snmp_port || 161,
    snmp_v3_user: device?.snmp_v3_user || '',
    snmp_v3_auth_protocol: device?.snmp_v3_auth_protocol || 'SHA',
    snmp_v3_priv_protocol: device?.snmp_v3_priv_protocol || 'AES',
    // Write-only: the API never returns these, so blank means "keep".
    snmp_community: '',
    snmp_v3_auth_key: '',
    snmp_v3_priv_key: '',
  });

  const saveMutation = useMutation({
    mutationFn: async () => {
      // Secrets left blank must not be sent, or an edit would overwrite the
      // stored value with an empty one.
      const payload: Record<string, any> = { ...formData };
      for (const secret of [
        'password',
        'snmp_community',
        'snmp_v3_auth_key',
        'snmp_v3_priv_key',
      ]) {
        if (!payload[secret]) delete payload[secret];
      }

      if (device) {
        await api.put(`/devices/${device.id}`, payload as DeviceUpdate);
      } else {
        await api.post('/devices', payload as DeviceCreate);
      }
    },
    onSuccess: () => {
      toast.success(device ? 'Device updated successfully' : 'Device added successfully');
      onSuccess();
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.detail || 'Failed to save device');
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    saveMutation.mutate();
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const { name, value, type } = e.target;
    setFormData((prev) => ({
      ...prev,
      [name]: type === 'number' ? parseInt(value) : value,
    }));
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
        <div className="p-6">
          <h2 className="text-2xl font-bold text-gray-900 mb-6">
            {device ? 'Edit Device' : 'Add Device'}
          </h2>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Hostname *
                </label>
                <input
                  type="text"
                  name="hostname"
                  required
                  value={formData.hostname}
                  onChange={handleChange}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  placeholder="router-01"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  IP Address *
                </label>
                <input
                  type="text"
                  name="ip_address"
                  required
                  value={formData.ip_address}
                  onChange={handleChange}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  placeholder="192.168.1.1"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Device Type *
                </label>
                <select
                  name="device_type"
                  required
                  value={formData.device_type}
                  onChange={handleChange}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                >
                  {Object.entries(DEVICE_TYPES).map(([key, label]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Transport
                </label>
                <select
                  name="transport"
                  value={formData.transport}
                  onChange={(e) => {
                    const transport = e.target.value as Transport;
                    setFormData((prev) => ({
                      ...prev,
                      transport,
                      // Follow the conventional port for the transport unless
                      // the user has already moved it somewhere else.
                      port:
                        transport === 'telnet' && prev.port === 22
                          ? 23
                          : transport === 'ssh' && prev.port === 23
                          ? 22
                          : prev.port,
                      snmp_version:
                        transport === 'snmp' ? prev.snmp_version || '2c' : prev.snmp_version,
                    }));
                  }}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                >
                  {Object.entries(TRANSPORTS).map(([key, label]) => (
                    <option key={key} value={key}>
                      {label}
                    </option>
                  ))}
                </select>
                {formData.transport === 'snmp' && (
                  <p className="text-xs text-amber-700 mt-1">
                    SNMP is read-only: this device can be discovered and
                    inventoried, but not backed up.
                  </p>
                )}
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  {formData.transport === 'telnet' ? 'Telnet' : 'SSH'} Port
                </label>
                <input
                  type="number"
                  name="port"
                  value={formData.port}
                  onChange={handleChange}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  placeholder={formData.transport === 'telnet' ? '23' : '22'}
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Username *
                </label>
                <input
                  type="text"
                  name="username"
                  required
                  value={formData.username}
                  onChange={handleChange}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  placeholder="admin"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Password {device ? '' : '*'}
                </label>
                <input
                  type="password"
                  name="password"
                  required={!device}
                  value={formData.password}
                  onChange={handleChange}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  placeholder={device ? 'Leave blank to keep current' : '••••••••'}
                />
              </div>

              <div className="md:col-span-2">
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Enable Secret (optional)
                </label>
                <input
                  type="password"
                  name="enable_secret"
                  value={formData.enable_secret}
                  onChange={handleChange}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  placeholder="For Cisco devices requiring enable mode"
                />
              </div>

              {/* SNMP is also useful alongside the CLI for discovery, so these
                  are offered whenever a version is picked, not only when the
                  transport itself is SNMP. */}
              <div className="md:col-span-2 border-t pt-4">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      SNMP Version
                    </label>
                    <select
                      name="snmp_version"
                      value={formData.snmp_version ?? ''}
                      onChange={(e) =>
                        setFormData((prev) => ({
                          ...prev,
                          snmp_version: (e.target.value || null) as
                            | '1'
                            | '2c'
                            | '3'
                            | null,
                        }))
                      }
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    >
                      <option value="">Not configured</option>
                      <option value="1">v1</option>
                      <option value="2c">v2c</option>
                      <option value="3">v3</option>
                    </select>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">
                      SNMP Port
                    </label>
                    <input
                      type="number"
                      name="snmp_port"
                      value={formData.snmp_port}
                      onChange={handleChange}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                      placeholder="161"
                    />
                  </div>

                  {(formData.snmp_version === '1' ||
                    formData.snmp_version === '2c') && (
                    <div className="md:col-span-2">
                      <label className="block text-sm font-medium text-gray-700 mb-1">
                        Community
                      </label>
                      <input
                        type="password"
                        name="snmp_community"
                        value={formData.snmp_community}
                        onChange={handleChange}
                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                        placeholder={device ? 'Leave blank to keep current' : 'public'}
                      />
                    </div>
                  )}

                  {formData.snmp_version === '3' && (
                    <>
                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">
                          v3 Username
                        </label>
                        <input
                          type="text"
                          name="snmp_v3_user"
                          value={formData.snmp_v3_user ?? ''}
                          onChange={handleChange}
                          className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                        />
                      </div>

                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">
                            Auth
                          </label>
                          <select
                            name="snmp_v3_auth_protocol"
                            value={formData.snmp_v3_auth_protocol ?? 'SHA'}
                            onChange={handleChange}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                          >
                            <option value="SHA">SHA</option>
                            <option value="MD5">MD5</option>
                          </select>
                        </div>
                        <div>
                          <label className="block text-sm font-medium text-gray-700 mb-1">
                            Privacy
                          </label>
                          <select
                            name="snmp_v3_priv_protocol"
                            value={formData.snmp_v3_priv_protocol ?? 'AES'}
                            onChange={handleChange}
                            className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                          >
                            <option value="AES">AES</option>
                            <option value="DES">DES</option>
                          </select>
                        </div>
                      </div>

                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">
                          Auth key
                        </label>
                        <input
                          type="password"
                          name="snmp_v3_auth_key"
                          value={formData.snmp_v3_auth_key}
                          onChange={handleChange}
                          className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                          placeholder={device ? 'Leave blank to keep current' : ''}
                        />
                      </div>

                      <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">
                          Privacy key
                        </label>
                        <input
                          type="password"
                          name="snmp_v3_priv_key"
                          value={formData.snmp_v3_priv_key}
                          onChange={handleChange}
                          className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                          placeholder={device ? 'Leave blank to keep current' : ''}
                        />
                      </div>
                    </>
                  )}
                </div>
              </div>

              <div className="md:col-span-2">
                <label className="flex items-center">
                  <input
                    type="checkbox"
                    name="is_active"
                    checked={formData.is_active}
                    onChange={(e) =>
                      setFormData((prev) => ({ ...prev, is_active: e.target.checked }))
                    }
                    className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                  />
                  <span className="ml-2 text-sm text-gray-700">Active (enable backups)</span>
                </label>
              </div>
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
                {saveMutation.isPending ? 'Saving...' : device ? 'Update Device' : 'Add Device'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
};
