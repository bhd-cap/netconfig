/**
 * Inventory Page
 *
 * Every host seen on a switch port, when it was first seen and when it was
 * last seen, with the vendor resolved from the MAC's OUI.
 *
 * Rows are aged rather than deleted, so "where did that laptop go" has an
 * answer: last seen on port 12, three weeks ago.
 */
import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { formatDistanceToNow } from 'date-fns';
import { toast } from 'react-hot-toast';
import {
  Database,
  Download,
  Loader2,
  Pencil,
  RefreshCw,
  Search,
  Tag,
} from 'lucide-react';
import api from '../lib/api';
import { usePermissions } from '../hooks/usePermissions';
import {
  Device,
  HostInventoryEntry,
  OuiStatus,
  PaginatedResponse,
} from '../types';

const PAGE_SIZE = 50;

function relative(value?: string | null): string {
  if (!value) return '—';
  try {
    return formatDistanceToNow(new Date(value), { addSuffix: true });
  } catch {
    return value;
  }
}

export const Inventory: React.FC = () => {
  const queryClient = useQueryClient();
  const { can } = usePermissions();
  const canWrite = can('inventory:write');
  const canImportOui = can('settings:write');

  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [deviceId, setDeviceId] = useState<number | ''>('');
  const [vlan, setVlan] = useState('');
  const [vendor, setVendor] = useState('');
  const [activeOnly, setActiveOnly] = useState(true);
  const [seenWithin, setSeenWithin] = useState('');
  const [editing, setEditing] = useState<HostInventoryEntry | null>(null);

  const { data: devices } = useQuery<PaginatedResponse<Device>>({
    queryKey: ['devices', 'for-inventory'],
    queryFn: async () => (await api.get('/devices', { params: { limit: 100 } })).data,
  });

  const { data: ouiStatus } = useQuery<OuiStatus>({
    queryKey: ['oui-status'],
    queryFn: async () => (await api.get('/inventory/oui/status')).data,
  });

  const { data, isLoading, isFetching } = useQuery<
    PaginatedResponse<HostInventoryEntry>
  >({
    queryKey: [
      'inventory',
      page,
      search,
      deviceId,
      vlan,
      vendor,
      activeOnly,
      seenWithin,
    ],
    queryFn: async () => {
      const params: Record<string, any> = {
        skip: (page - 1) * PAGE_SIZE,
        limit: PAGE_SIZE,
        active_only: activeOnly,
      };
      if (search) params.search = search;
      if (deviceId) params.device_id = deviceId;
      if (vlan) params.vlan = Number(vlan);
      if (vendor) params.vendor = vendor;
      if (seenWithin) params.seen_within_hours = Number(seenWithin);

      return (await api.get('/inventory', { params })).data;
    },
  });

  const refreshMutation = useMutation({
    mutationFn: async () => (await api.post('/inventory/refresh', {})).data,
    onSuccess: () =>
      toast.success('Inventory refresh queued; results appear as devices respond'),
  });

  const importOui = useMutation({
    mutationFn: async (source: string) =>
      (await api.post('/inventory/oui/import', { source })).data,
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['oui-status'] });
      toast.success(result.message);
    },
  });

  const backfill = useMutation({
    mutationFn: async () => (await api.post('/inventory/oui/backfill')).data,
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['inventory'] });
      toast.success(result.message);
    },
  });

  const annotate = useMutation({
    mutationFn: async (payload: {
      id: number;
      hostname: string;
      notes: string;
    }) =>
      (
        await api.patch(`/inventory/${payload.id}`, {
          hostname: payload.hostname,
          notes: payload.notes,
        })
      ).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['inventory'] });
      setEditing(null);
      toast.success('Saved');
    },
  });

  const exportCsv = async () => {
    const params: Record<string, any> = { active_only: activeOnly };
    if (deviceId) params.device_id = deviceId;

    const response = await api.get('/inventory/reports/export', {
      params,
      responseType: 'blob',
    });

    const url = URL.createObjectURL(new Blob([response.data]));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `inventory_${new Date().toISOString().slice(0, 10)}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const applySearch = (event: React.FormEvent) => {
    event.preventDefault();
    setPage(1);
    setSearch(searchInput.trim());
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Host Inventory</h1>
          <p className="text-gray-600">
            What is plugged into which switch port, and when it was last seen.
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            onClick={exportCsv}
            className="inline-flex items-center px-3 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50"
          >
            <Download className="h-4 w-4 mr-2" />
            Export CSV
          </button>

          {canWrite && (
            <button
              onClick={() => refreshMutation.mutate()}
              disabled={refreshMutation.isPending}
              className="inline-flex items-center px-3 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50"
            >
              <RefreshCw className="h-4 w-4 mr-2" />
              Refresh from devices
            </button>
          )}
        </div>
      </div>

      {/* OUI status */}
      {ouiStatus && (
        <div className="bg-white rounded-lg shadow p-4 flex flex-wrap items-center gap-4 text-sm">
          <Tag className="h-5 w-5 text-gray-400" />
          <span className="text-gray-900 font-medium">
            {ouiStatus.prefixes.toLocaleString()} OUI prefixes loaded
          </span>
          <span className="text-gray-500 flex-1 min-w-[16rem]">
            {ouiStatus.prefixes < 1000 ? ouiStatus.note : 'Vendor lookup is populated.'}
          </span>

          {canImportOui && (
            <div className="flex flex-wrap gap-2">
              {ouiStatus.system_file && (
                <button
                  onClick={() => importOui.mutate('system')}
                  disabled={importOui.isPending}
                  className="px-3 py-1.5 border border-gray-300 rounded text-xs hover:bg-gray-50 disabled:opacity-50"
                  title={ouiStatus.system_file}
                >
                  Import from this host
                </button>
              )}
              <button
                onClick={() => importOui.mutate('ieee')}
                disabled={importOui.isPending}
                className="px-3 py-1.5 border border-gray-300 rounded text-xs hover:bg-gray-50 disabled:opacity-50"
              >
                {importOui.isPending ? 'Importing…' : 'Import IEEE registry'}
              </button>
              {canWrite && (
                <button
                  onClick={() => backfill.mutate()}
                  disabled={backfill.isPending}
                  className="px-3 py-1.5 border border-gray-300 rounded text-xs hover:bg-gray-50 disabled:opacity-50"
                >
                  Resolve unknown vendors
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {/* Filters */}
      <div className="bg-white rounded-lg shadow p-4">
        <div className="grid grid-cols-1 md:grid-cols-6 gap-3">
          <form onSubmit={applySearch} className="md:col-span-2 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
            <input
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="MAC, IP or host name"
              className="w-full pl-9 pr-3 py-2 border border-gray-300 rounded-lg text-sm"
            />
          </form>

          <select
            value={deviceId}
            onChange={(event) => {
              setPage(1);
              setDeviceId(event.target.value ? Number(event.target.value) : '');
            }}
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
          >
            <option value="">Every switch</option>
            {devices?.items.map((device) => (
              <option key={device.id} value={device.id}>
                {device.hostname}
              </option>
            ))}
          </select>

          <input
            value={vlan}
            onChange={(event) => {
              setPage(1);
              setVlan(event.target.value.replace(/\D/g, ''));
            }}
            placeholder="VLAN"
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
          />

          <input
            value={vendor}
            onChange={(event) => {
              setPage(1);
              setVendor(event.target.value);
            }}
            placeholder="Vendor"
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
          />

          <select
            value={seenWithin}
            onChange={(event) => {
              setPage(1);
              setSeenWithin(event.target.value);
            }}
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
          >
            <option value="">Any time</option>
            <option value="1">Last hour</option>
            <option value="24">Last 24 hours</option>
            <option value="168">Last week</option>
          </select>
        </div>

        <div className="flex items-center gap-4 mt-3 text-sm">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={activeOnly}
              onChange={(event) => {
                setPage(1);
                setActiveOnly(event.target.checked);
              }}
            />
            Only hosts still being seen
          </label>

          <span className="ml-auto text-gray-500">
            {data ? `${data.total.toLocaleString()} entries` : ''}
          </span>
        </div>
      </div>

      {/* Table */}
      <div className="bg-white rounded-lg shadow overflow-hidden">
        {isLoading ? (
          <div className="p-12 flex justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
          </div>
        ) : !data?.items.length ? (
          <div className="p-12 text-center text-gray-500">
            <Database className="h-10 w-10 mx-auto mb-3 text-gray-300" />
            <p className="font-medium text-gray-900">Nothing here yet</p>
            <p className="text-sm mt-1">
              Run a discovery crawl with inventory collection, or refresh from
              devices.
            </p>
          </div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200 text-sm">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                      MAC
                    </th>
                    <th className="px-4 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                      Vendor
                    </th>
                    <th className="px-4 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                      Address
                    </th>
                    <th className="px-4 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                      Switch
                    </th>
                    <th className="px-4 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                      Port
                    </th>
                    <th className="px-4 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                      VLAN
                    </th>
                    <th className="px-4 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                      First seen
                    </th>
                    <th className="px-4 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                      Last seen
                    </th>
                    {canWrite && <th className="px-4 py-3" />}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {data.items.map((row) => (
                    <tr key={row.id} className={row.is_active ? '' : 'bg-gray-50'}>
                      <td className="px-4 py-3 font-mono text-xs text-gray-900">
                        {row.mac_address}
                      </td>
                      <td className="px-4 py-3 text-gray-700 max-w-[14rem] truncate">
                        {row.vendor || (
                          <span className="text-gray-400">unknown</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-gray-700">
                        {row.ip_address || '—'}
                        {row.hostname && (
                          <p className="text-xs text-gray-500">{row.hostname}</p>
                        )}
                      </td>
                      <td className="px-4 py-3 text-gray-900">
                        {row.device_hostname}
                      </td>
                      <td className="px-4 py-3 text-gray-700">{row.interface}</td>
                      <td className="px-4 py-3 text-gray-700">
                        {row.vlan ? row.vlan : '—'}
                      </td>
                      <td className="px-4 py-3 text-gray-500">
                        {relative(row.first_seen)}
                      </td>
                      <td className="px-4 py-3 text-gray-500">
                        {relative(row.last_seen)}
                        {!row.is_active && (
                          <span className="ml-2 px-1.5 py-0.5 bg-gray-200 text-gray-700 rounded text-xs">
                            gone
                          </span>
                        )}
                      </td>
                      {canWrite && (
                        <td className="px-4 py-3 text-right">
                          <button
                            onClick={() => setEditing(row)}
                            className="text-gray-400 hover:text-blue-600"
                            title="Add a name or note"
                          >
                            <Pencil className="h-4 w-4" />
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="px-4 py-3 border-t border-gray-200 flex items-center justify-between text-sm">
              <span className="text-gray-600">
                Page {data.page} of {data.total_pages}
              </span>
              <div className="flex gap-2">
                <button
                  onClick={() => setPage((current) => Math.max(1, current - 1))}
                  disabled={page <= 1 || isFetching}
                  className="px-3 py-1.5 border border-gray-300 rounded disabled:opacity-50"
                >
                  Previous
                </button>
                <button
                  onClick={() => setPage((current) => current + 1)}
                  disabled={page >= data.total_pages || isFetching}
                  className="px-3 py-1.5 border border-gray-300 rounded disabled:opacity-50"
                >
                  Next
                </button>
              </div>
            </div>
          </>
        )}
      </div>

      {/* Annotate */}
      {editing && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-1">
              Annotate host
            </h3>
            <p className="font-mono text-xs text-gray-500 mb-4">
              {editing.mac_address} on {editing.device_hostname}{' '}
              {editing.interface}
            </p>

            <form
              onSubmit={(event) => {
                event.preventDefault();
                const form = new FormData(event.currentTarget);
                annotate.mutate({
                  id: editing.id,
                  hostname: String(form.get('hostname') ?? ''),
                  notes: String(form.get('notes') ?? ''),
                });
              }}
              className="space-y-4"
            >
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Name
                </label>
                <input
                  name="hostname"
                  defaultValue={editing.hostname ?? ''}
                  placeholder="e.g. reception-printer"
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Notes
                </label>
                <textarea
                  name="notes"
                  defaultValue={editing.notes ?? ''}
                  rows={3}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg"
                />
              </div>

              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setEditing(null)}
                  className="px-4 py-2 border border-gray-300 rounded-lg"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={annotate.isPending}
                  className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
                >
                  Save
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
