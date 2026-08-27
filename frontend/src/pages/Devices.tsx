/**
 * Devices Page - Network device management
 */
import React, { useState } from 'react';
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
  XCircle
} from 'lucide-react';
import { toast } from 'react-hot-toast';
import api from '../lib/api';
import {
  Device,
  DeviceCreate,
  DeviceUpdate,
  PaginatedResponse,
  DEVICE_TYPES,
  TRANSPORTS,
  Transport,
} from '../types';

export const Devices: React.FC = () => {
  const [showAddModal, setShowAddModal] = useState(false);
  const [editingDevice, setEditingDevice] = useState<Device | null>(null);
  const [page, setPage] = useState(1);
  const [limit] = useState(20);
  const queryClient = useQueryClient();

  // Fetch devices
  const { data: devicesData, isLoading } = useQuery({
    queryKey: ['devices', page, limit],
    queryFn: async () => {
      const skip = (page - 1) * limit;
      const response = await api.get<PaginatedResponse<Device>>(`/devices?skip=${skip}&limit=${limit}`);
      return response.data;
    },
  });

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

  // Trigger backup mutation
  const backupMutation = useMutation({
    mutationFn: async (deviceId: number) => {
      const response = await api.post(`/backups/trigger`, { device_ids: [deviceId] });
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

  const handleBackup = (device: Device) => {
    backupMutation.mutate(device.id);
  };

  const handleTest = (device: Device) => {
    testMutation.mutate(device.id);
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

      {/* Devices List */}
      {isLoading ? (
        <div className="bg-white rounded-lg shadow p-12 text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto"></div>
          <p className="mt-4 text-gray-600">Loading devices...</p>
        </div>
      ) : devicesData && devicesData.items.length > 0 ? (
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Device
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  IP Address
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Type
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Status
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Last Backup
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {devicesData.items.map((device) => (
                <tr key={device.id} className="hover:bg-gray-50">
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className="flex items-center">
                      <Server className="h-5 w-5 text-gray-400 mr-3" />
                      <div>
                        <div className="text-sm font-medium text-gray-900">{device.hostname}</div>
                        <div className="text-sm text-gray-500">User: {device.username}</div>
                      </div>
                    </div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                    {device.ip_address}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">
                    {DEVICE_TYPES[device.device_type as keyof typeof DEVICE_TYPES] || device.device_type}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    {getStatusBadge(device)}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                    {device.last_backup_at
                      ? new Date(device.last_backup_at).toLocaleString()
                      : 'Never'}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                    <div className="flex justify-end gap-2">
                      <button
                        onClick={() => handleTest(device)}
                        className="text-purple-600 hover:text-purple-900"
                        title="Test Connection"
                      >
                        <Activity className="h-5 w-5" />
                      </button>
                      <button
                        onClick={() => handleBackup(device)}
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
          <h3 className="text-lg font-medium text-gray-900 mb-2">No devices yet</h3>
          <p className="text-gray-600 mb-4">
            Get started by adding your first network device.
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
