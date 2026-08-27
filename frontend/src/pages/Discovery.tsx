/**
 * Discovery Page
 *
 * Starts a crawl from a seed device and shows what it found: the adjacencies
 * learned from LLDP and CDP, and the history of previous crawls.
 */
import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { formatDistanceToNow } from 'date-fns';
import { toast } from 'react-hot-toast';
import { Link } from 'react-router-dom';
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  KeyRound,
  Loader2,
  Play,
  RefreshCw,
  Search,
  Trash2,
} from 'lucide-react';
import api from '../lib/api';
import { usePermissions } from '../hooks/usePermissions';
import {
  CredentialSummary,
  Device,
  DiscoveryRun,
  Neighbor,
  PaginatedResponse,
} from '../types';

const RUNNING_STATUSES = new Set(['running', 'pending']);

function relative(value?: string | null): string {
  if (!value) return '—';
  try {
    return formatDistanceToNow(new Date(value), { addSuffix: true });
  } catch {
    return value;
  }
}

export const Discovery: React.FC = () => {
  const queryClient = useQueryClient();
  const { can } = usePermissions();
  const canRun = can('discovery:run');
  const canEdit = can('discovery:write');

  const [seedDeviceId, setSeedDeviceId] = useState<number | ''>('');
  const [maxHops, setMaxHops] = useState(2);
  const [autoAdd, setAutoAdd] = useState(false);
  const [collectInventory, setCollectInventory] = useState(true);

  const [deviceFilter, setDeviceFilter] = useState<number | ''>('');
  const [protocolFilter, setProtocolFilter] = useState('');
  const [activeOnly, setActiveOnly] = useState(true);

  const { data: devices } = useQuery<PaginatedResponse<Device>>({
    queryKey: ['devices', 'for-discovery'],
    queryFn: async () =>
      (await api.get('/devices', { params: { limit: 100 } })).data,
  });

  // Worth knowing before a crawl rather than after: with no CLI credentials
  // it maps the topology but authenticates nothing, so nothing it finds
  // becomes eligible for backup.
  const { data: credentials } = useQuery<CredentialSummary>({
    queryKey: ['credential-summary'],
    queryFn: async () => (await api.get('/credentials/summary')).data,
    retry: false,
  });

  const { data: runs } = useQuery<DiscoveryRun[]>({
    queryKey: ['discovery-runs'],
    queryFn: async () => (await api.get('/discovery/runs', { params: { limit: 10 } })).data,
    // A queued crawl finishes on a worker, so poll while one is in flight.
    refetchInterval: (query) =>
      (query.state.data ?? []).some((run: DiscoveryRun) =>
        RUNNING_STATUSES.has(run.status)
      )
        ? 5000
        : false,
  });

  const {
    data: neighbors,
    isLoading: neighborsLoading,
    refetch: refetchNeighbors,
    isFetching: neighborsFetching,
  } = useQuery<Neighbor[]>({
    queryKey: ['neighbors', deviceFilter, protocolFilter, activeOnly],
    queryFn: async () => {
      const params: Record<string, any> = { active_only: activeOnly, limit: 500 };
      if (deviceFilter) params.device_id = deviceFilter;
      if (protocolFilter) params.protocol = protocolFilter;
      return (await api.get('/discovery/neighbors', { params })).data;
    },
  });

  const runMutation = useMutation({
    mutationFn: async () =>
      (
        await api.post('/discovery/run', {
          seed_device_id: seedDeviceId,
          max_hops: maxHops,
          auto_add: autoAdd,
          collect_inventory: collectInventory,
        })
      ).data,
    onSuccess: (data) => {
      toast.success(data.message ?? 'Discovery started');
      queryClient.invalidateQueries({ queryKey: ['discovery-runs'] });
    },
  });

  const deleteNeighbor = useMutation({
    mutationFn: async (id: number) => api.delete(`/discovery/neighbors/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['neighbors'] });
      queryClient.invalidateQueries({ queryKey: ['topology'] });
      toast.success('Adjacency removed');
    },
  });

  const unmanaged = (neighbors ?? []).filter((row) => !row.remote_device_id);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Discovery</h1>
        <p className="text-gray-600">
          Walk the network from one device, following LLDP and CDP neighbours.
        </p>
      </div>

      {/* Start a crawl */}
      {canRun && (
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">
            Run a discovery
          </h2>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div className="md:col-span-2">
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Start from
              </label>
              <select
                value={seedDeviceId}
                onChange={(event) =>
                  setSeedDeviceId(event.target.value ? Number(event.target.value) : '')
                }
                className="w-full px-3 py-2 border border-gray-300 rounded-lg"
              >
                <option value="">Choose a device…</option>
                {devices?.items.map((device) => (
                  <option key={device.id} value={device.id}>
                    {device.hostname} ({device.ip_address})
                  </option>
                ))}
              </select>
              <p className="text-xs text-gray-500 mt-1">
                Everything else is found by following this device's neighbours.
              </p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Hops
              </label>
              <input
                type="number"
                min={0}
                max={10}
                value={maxHops}
                onChange={(event) => setMaxHops(Number(event.target.value))}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg"
              />
              <p className="text-xs text-gray-500 mt-1">
                How far from the seed to walk.
              </p>
            </div>

            <div className="flex items-end">
              <button
                onClick={() => runMutation.mutate()}
                disabled={!seedDeviceId || runMutation.isPending}
                className="w-full inline-flex items-center justify-center px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
              >
                {runMutation.isPending ? (
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                ) : (
                  <Play className="h-4 w-4 mr-2" />
                )}
                Start
              </button>
            </div>
          </div>

          {/* What the crawl will authenticate with */}
          {credentials && (
            <div
              className={`mt-4 rounded border p-3 text-sm flex items-start gap-2 ${
                credentials.cli > 0
                  ? 'border-gray-200 bg-gray-50'
                  : 'border-amber-200 bg-amber-50'
              }`}
            >
              {credentials.cli > 0 ? (
                <KeyRound className="h-4 w-4 text-gray-500 mt-0.5 shrink-0" />
              ) : (
                <AlertTriangle className="h-4 w-4 text-amber-600 mt-0.5 shrink-0" />
              )}
              <div>
                {credentials.cli > 0 ? (
                  <p className="text-gray-700">
                    {credentials.cli} CLI login
                    {credentials.cli === 1 ? '' : 's'} and {credentials.snmp} SNMP
                    credential{credentials.snmp === 1 ? '' : 's'} will be tried,
                    in vault order, against everything found.
                  </p>
                ) : (
                  <p className="text-amber-900">
                    No CLI logins are in the vault. The crawl will still map the
                    topology, but nothing it finds can authenticate, so nothing
                    becomes eligible for backup.
                  </p>
                )}
                <Link
                  to="/settings"
                  className="text-xs text-blue-600 hover:text-blue-800"
                >
                  Manage the credential vault →
                </Link>
              </div>
            </div>
          )}

          <div className="flex flex-wrap gap-6 mt-4 text-sm">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={autoAdd}
                onChange={(event) => setAutoAdd(event.target.checked)}
              />
              Add discovered neighbours as devices
              <span className="text-xs text-gray-500">
                (each is probed with the vault; only one that logs in is added to
                the backup list)
              </span>
            </label>

            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={collectInventory}
                onChange={(event) => setCollectInventory(event.target.checked)}
              />
              Also collect MAC tables and ARP
            </label>
          </div>
        </div>
      )}

      {/* Recent runs */}
      <div className="bg-white rounded-lg shadow">
        <div className="px-6 py-4 border-b border-gray-200">
          <h2 className="text-lg font-semibold text-gray-900">Recent crawls</h2>
        </div>

        {!runs?.length ? (
          <p className="p-6 text-sm text-gray-500">No crawls have been run yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                    Started
                  </th>
                  <th className="px-6 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                    Status
                  </th>
                  <th className="px-6 py-3 text-right font-medium text-gray-500 uppercase text-xs">
                    Probed
                  </th>
                  <th className="px-6 py-3 text-right font-medium text-gray-500 uppercase text-xs">
                    Links
                  </th>
                  <th className="px-6 py-3 text-right font-medium text-gray-500 uppercase text-xs">
                    Hosts
                  </th>
                  <th className="px-6 py-3 text-right font-medium text-gray-500 uppercase text-xs">
                    Added
                  </th>
                  <th className="px-6 py-3 text-right font-medium text-gray-500 uppercase text-xs">
                    Duration
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {runs.map((run) => (
                  <tr key={run.id}>
                    <td className="px-6 py-3 text-gray-900">
                      {relative(run.started_at)}
                    </td>
                    <td className="px-6 py-3">
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                          run.status === 'completed'
                            ? 'bg-emerald-100 text-emerald-800'
                            : run.status === 'failed'
                            ? 'bg-red-100 text-red-800'
                            : 'bg-blue-100 text-blue-800'
                        }`}
                      >
                        {RUNNING_STATUSES.has(run.status) && (
                          <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                        )}
                        {run.status}
                      </span>
                      {run.error_message && (
                        <p className="text-xs text-red-600 mt-1">
                          {run.error_message}
                        </p>
                      )}
                    </td>
                    <td className="px-6 py-3 text-right">{run.devices_probed}</td>
                    <td className="px-6 py-3 text-right">{run.neighbors_found}</td>
                    <td className="px-6 py-3 text-right">{run.hosts_found}</td>
                    <td className="px-6 py-3 text-right">{run.devices_created}</td>
                    <td className="px-6 py-3 text-right text-gray-500">
                      {run.duration != null ? `${run.duration}s` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Neighbours */}
      <div className="bg-white rounded-lg shadow">
        <div className="px-6 py-4 border-b border-gray-200 flex flex-wrap items-center gap-4">
          <h2 className="text-lg font-semibold text-gray-900 mr-auto">
            Adjacencies
            {neighbors && (
              <span className="ml-2 text-sm font-normal text-gray-500">
                {neighbors.length} link{neighbors.length === 1 ? '' : 's'}
                {unmanaged.length > 0 &&
                  `, ${unmanaged.length} to something not managed`}
              </span>
            )}
          </h2>

          <select
            value={deviceFilter}
            onChange={(event) =>
              setDeviceFilter(event.target.value ? Number(event.target.value) : '')
            }
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
          >
            <option value="">Every device</option>
            {devices?.items.map((device) => (
              <option key={device.id} value={device.id}>
                {device.hostname}
              </option>
            ))}
          </select>

          <select
            value={protocolFilter}
            onChange={(event) => setProtocolFilter(event.target.value)}
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
          >
            <option value="">LLDP and CDP</option>
            <option value="lldp">LLDP only</option>
            <option value="cdp">CDP only</option>
          </select>

          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={activeOnly}
              onChange={(event) => setActiveOnly(event.target.checked)}
            />
            Still seen
          </label>

          <button
            onClick={() => refetchNeighbors()}
            className="inline-flex items-center px-3 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50"
          >
            <RefreshCw
              className={`h-4 w-4 ${neighborsFetching ? 'animate-spin' : ''}`}
            />
          </button>
        </div>

        {neighborsLoading ? (
          <div className="p-12 flex justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
          </div>
        ) : !neighbors?.length ? (
          <div className="p-12 text-center text-gray-500">
            <Search className="h-10 w-10 mx-auto mb-3 text-gray-300" />
            <p>No adjacencies match those filters.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                    Device
                  </th>
                  <th className="px-6 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                    Local port
                  </th>
                  <th className="px-6 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                    Neighbour
                  </th>
                  <th className="px-6 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                    Remote port
                  </th>
                  <th className="px-6 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                    Platform
                  </th>
                  <th className="px-6 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                    Last seen
                  </th>
                  {canEdit && <th className="px-6 py-3" />}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {neighbors.map((row) => (
                  <tr key={row.id} className={row.is_active ? '' : 'bg-gray-50'}>
                    <td className="px-6 py-3 font-medium text-gray-900">
                      {row.device_hostname}
                    </td>
                    <td className="px-6 py-3 text-gray-600">{row.local_interface}</td>
                    <td className="px-6 py-3">
                      <div className="flex items-center gap-2">
                        <span className="text-gray-900">{row.remote_hostname}</span>
                        {row.remote_device_id ? (
                          <CheckCircle2
                            className="h-4 w-4 text-emerald-500 shrink-0"
                            aria-label="Managed device"
                          />
                        ) : (
                          <AlertCircle
                            className="h-4 w-4 text-amber-500 shrink-0"
                            aria-label="Not managed"
                          />
                        )}
                      </div>
                      {row.remote_mgmt_ip && (
                        <p className="text-xs text-gray-500">{row.remote_mgmt_ip}</p>
                      )}
                    </td>
                    <td className="px-6 py-3 text-gray-600">
                      {row.remote_interface || '—'}
                    </td>
                    <td className="px-6 py-3 text-gray-500 max-w-xs truncate">
                      {row.remote_platform || '—'}
                    </td>
                    <td className="px-6 py-3 text-gray-500">
                      {relative(row.last_seen)}
                      <span className="ml-2 text-xs uppercase text-gray-400">
                        {row.protocol}
                      </span>
                    </td>
                    {canEdit && (
                      <td className="px-6 py-3 text-right">
                        <button
                          onClick={() => {
                            if (window.confirm('Remove this adjacency?')) {
                              deleteNeighbor.mutate(row.id);
                            }
                          }}
                          className="text-gray-400 hover:text-red-600"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};
