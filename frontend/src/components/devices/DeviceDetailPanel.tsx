/**
 * Everything known about one device
 *
 * Opened by clicking a device name on either the Devices page or the
 * Inventory page. Shows what the probe learned about the device, the outcome
 * of each transport it tried, its neighbours, and how many hosts have been
 * seen on its ports.
 *
 * The probe results matter as much as the facts: "SSH refused, telnet timed
 * out, 4 credentials tried" is actionable, where an inactive flag on its own
 * leaves an operator guessing.
 */
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { format, formatDistanceToNow } from 'date-fns';
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Info,
  Loader2,
  Network,
  Server,
  ShieldCheck,
  ShieldX,
  X,
  XCircle,
} from 'lucide-react';
import api from '../../lib/api';
import { AuthStatus, DeviceDetail } from '../../types';

interface Props {
  deviceId: number;
  onClose: () => void;
}

function absolute(value?: string | null): string {
  if (!value) return '—';
  try {
    return format(new Date(value), 'd MMM yyyy HH:mm');
  } catch {
    return value;
  }
}

function relative(value?: string | null): string {
  if (!value) return 'never';
  try {
    return formatDistanceToNow(new Date(value), { addSuffix: true });
  } catch {
    return value;
  }
}

function uptime(seconds?: number | null): string {
  if (!seconds) return '—';
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  if (days) return `${days}d ${hours}h`;
  return `${hours}h`;
}

const AUTH_LABELS: Record<AuthStatus, string> = {
  never: 'Not tried yet',
  success: 'Authenticated',
  auth_failed: 'Login refused',
  unreachable: 'Nothing answered',
  error: 'Probe error',
};

const Row: React.FC<{ label: string; children: React.ReactNode }> = ({
  label,
  children,
}) => (
  <div className="flex justify-between gap-4 py-1.5 border-b border-gray-100 last:border-0">
    <dt className="text-gray-500 shrink-0">{label}</dt>
    <dd className="text-gray-900 text-right break-words min-w-0">{children}</dd>
  </div>
);

const ProbeBadge: React.FC<{ result: string }> = ({ result }) => {
  const tone =
    result === 'success'
      ? 'bg-emerald-100 text-emerald-800'
      : result === 'auth_failed'
      ? 'bg-amber-100 text-amber-800'
      : 'bg-gray-200 text-gray-700';

  const Icon =
    result === 'success' ? CheckCircle2 : result === 'auth_failed' ? AlertTriangle : XCircle;

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${tone}`}
    >
      <Icon className="h-3 w-3 mr-1" />
      {result.replace('_', ' ')}
    </span>
  );
};

export const DeviceDetailPanel: React.FC<Props> = ({ deviceId, onClose }) => {
  const { data, isLoading, error } = useQuery<DeviceDetail>({
    queryKey: ['device-detail', deviceId],
    queryFn: async () => (await api.get(`/devices/${deviceId}/detail`)).data,
  });

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-start justify-center z-50 p-4 overflow-y-auto">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-4xl my-8">
        <div className="flex items-start justify-between px-6 py-4 border-b border-gray-200">
          <div className="flex items-center gap-3 min-w-0">
            <Server className="h-6 w-6 text-gray-400 shrink-0" />
            <div className="min-w-0">
              <h2 className="text-lg font-semibold text-gray-900 truncate">
                {data?.device.hostname ?? 'Device'}
              </h2>
              {data && (
                <p className="text-sm text-gray-500">
                  {data.device.ip_address} · {data.device.device_type} ·{' '}
                  {data.device.transport}
                  {data.device.discovered && ' · discovered'}
                </p>
              )}
            </div>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X className="h-5 w-5" />
          </button>
        </div>

        {isLoading ? (
          <div className="p-16 flex justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
          </div>
        ) : error || !data ? (
          <div className="p-8 text-center text-gray-500">
            Could not load this device.
          </div>
        ) : (
          <div className="p-6 space-y-6">
            {/* Backup eligibility, stated plainly. Authenticating and being
                on the backup list are separate facts, and saying "not
                eligible" about a device sitting on the schedule would leave
                an operator wondering which one to believe. */}
            <div
              className={`rounded-lg border p-4 ${
                data.authentication.backup_eligible
                  ? 'border-emerald-200 bg-emerald-50'
                  : 'border-amber-200 bg-amber-50'
              }`}
            >
              <div className="flex items-start gap-3">
                {data.authentication.backup_eligible ? (
                  <ShieldCheck className="h-5 w-5 text-emerald-600 mt-0.5 shrink-0" />
                ) : (
                  <ShieldX className="h-5 w-5 text-amber-600 mt-0.5 shrink-0" />
                )}
                <div className="min-w-0">
                  <p
                    className={`text-sm font-medium ${
                      data.authentication.backup_eligible
                        ? 'text-emerald-900'
                        : 'text-amber-900'
                    }`}
                  >
                    {data.authentication.status === 'success'
                      ? data.device.is_active
                        ? 'Eligible for backup'
                        : 'Authenticated, but taken off the backup list'
                      : data.device.is_active
                      ? 'On the backup list, but no login has succeeded'
                      : 'Not eligible for backup'}
                    {' — '}
                    {AUTH_LABELS[data.authentication.status] ??
                      data.authentication.status}
                  </p>

                  <p className="text-xs mt-1 text-gray-600">
                    {data.authentication.credential_name
                      ? `Authenticated with '${data.authentication.credential_name}'`
                      : 'No credential has worked yet'}
                    {data.authentication.at &&
                      ` · last tried ${relative(data.authentication.at)}`}
                  </p>

                  {data.authentication.error && (
                    <pre className="mt-2 text-xs text-amber-900 whitespace-pre-wrap break-words font-mono">
                      {data.authentication.error}
                    </pre>
                  )}

                  {data.authentication.status !== 'success' && (
                    <p className="text-xs text-gray-600 mt-2">
                      It stays in the inventory and can still be crawled — only
                      configuration backup needs a working login.
                    </p>
                  )}
                </div>
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* What the device says about itself */}
              <section>
                <h3 className="flex items-center text-sm font-semibold text-gray-500 uppercase mb-2">
                  <Info className="h-4 w-4 mr-1.5" />
                  Discovered facts
                </h3>

                <dl className="text-sm">
                  <Row label="Model">{data.facts.model ?? '—'}</Row>
                  <Row label="Serial">{data.facts.serial_number ?? '—'}</Row>
                  <Row label="OS version">{data.facts.os_version ?? '—'}</Row>
                  <Row label="SNMP sysName">{data.facts.snmp_sysname ?? '—'}</Row>
                  <Row label="SNMP location">{data.facts.snmp_location ?? '—'}</Row>
                  <Row label="SNMP contact">{data.facts.snmp_contact ?? '—'}</Row>
                  <Row label="Uptime">{uptime(data.facts.snmp_uptime_seconds)}</Row>
                  <Row label="SNMP polled">
                    {relative(data.facts.snmp_last_polled_at)}
                  </Row>
                </dl>

                {data.facts.snmp_sysdescr && (
                  <details className="mt-2">
                    <summary className="text-xs text-blue-600 cursor-pointer">
                      System description
                    </summary>
                    <pre className="mt-1 text-xs text-gray-600 whitespace-pre-wrap break-words font-mono bg-gray-50 p-2 rounded">
                      {data.facts.snmp_sysdescr}
                    </pre>
                  </details>
                )}
              </section>

              {/* Device record and backup state */}
              <section>
                <h3 className="flex items-center text-sm font-semibold text-gray-500 uppercase mb-2">
                  <Clock className="h-4 w-4 mr-1.5" />
                  Record
                </h3>

                <dl className="text-sm">
                  <Row label="Location">{data.device.location ?? '—'}</Row>
                  <Row label="Login user">{data.device.username}</Row>
                  <Row label="Port">{data.device.port}</Row>
                  <Row label="On backup list">
                    {data.device.is_active ? 'Yes' : 'No'}
                  </Row>
                  <Row label="Last backup">
                    {relative(data.device.last_backup_at)}
                    {data.device.last_backup_status &&
                      ` (${data.device.last_backup_status})`}
                  </Row>
                  <Row label="Found by">
                    {data.device.discovery_source ?? 'entered by hand'}
                  </Row>
                  <Row label="Added">{absolute(data.device.created_at)}</Row>
                  <Row label="Hosts on its ports">
                    {data.hosts.active} active of {data.hosts.total}, across{' '}
                    {data.hosts.ports_in_use} port(s)
                  </Row>
                </dl>
              </section>
            </div>

            {/* Probe results */}
            <section>
              <h3 className="text-sm font-semibold text-gray-500 uppercase mb-2">
                Transports tried
              </h3>

              {data.probes.length === 0 ? (
                <p className="text-sm text-gray-500">
                  This device has not been probed yet. It is probed when a
                  discovery crawl reaches it.
                </p>
              ) : (
                <div className="overflow-x-auto border border-gray-200 rounded">
                  <table className="min-w-full divide-y divide-gray-200 text-sm">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                          Transport
                        </th>
                        <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                          Result
                        </th>
                        <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                          Credential
                        </th>
                        <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 uppercase">
                          Tried
                        </th>
                        <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                          Detail
                        </th>
                        <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                          When
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200">
                      {data.probes.map((row) => (
                        <tr key={row.transport}>
                          <td className="px-3 py-2 font-medium text-gray-900">
                            {row.transport}
                          </td>
                          <td className="px-3 py-2">
                            <ProbeBadge result={row.result} />
                          </td>
                          <td className="px-3 py-2 text-gray-700">
                            {row.credential_name ?? '—'}
                          </td>
                          <td className="px-3 py-2 text-right text-gray-700">
                            {row.attempts}
                          </td>
                          <td className="px-3 py-2 text-gray-600 max-w-md break-words">
                            {row.message ?? '—'}
                          </td>
                          <td className="px-3 py-2 text-gray-500">
                            {relative(row.probed_at)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {/* Neighbours */}
            <section>
              <h3 className="flex items-center text-sm font-semibold text-gray-500 uppercase mb-2">
                <Network className="h-4 w-4 mr-1.5" />
                Neighbours ({data.neighbors.length})
              </h3>

              {data.neighbors.length === 0 ? (
                <p className="text-sm text-gray-500">
                  No LLDP or CDP adjacencies recorded for this device.
                </p>
              ) : (
                <div className="overflow-x-auto max-h-64 overflow-y-auto border border-gray-200 rounded">
                  <table className="min-w-full divide-y divide-gray-200 text-sm">
                    <thead className="bg-gray-50 sticky top-0">
                      <tr>
                        <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                          Local port
                        </th>
                        <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                          Neighbour
                        </th>
                        <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                          Remote port
                        </th>
                        <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                          Platform
                        </th>
                        <th className="px-3 py-2 text-left text-xs font-medium text-gray-500 uppercase">
                          Seen
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200">
                      {data.neighbors.map((row, index) => (
                        <tr
                          key={`${row.local_interface}-${row.remote_hostname}-${index}`}
                          className={row.is_active ? '' : 'bg-gray-50'}
                        >
                          <td className="px-3 py-2 text-gray-700">
                            {row.local_interface}
                          </td>
                          <td className="px-3 py-2 text-gray-900">
                            {row.remote_hostname}
                            {row.remote_mgmt_ip && (
                              <span className="text-xs text-gray-500 ml-1">
                                ({row.remote_mgmt_ip})
                              </span>
                            )}
                          </td>
                          <td className="px-3 py-2 text-gray-700">
                            {row.remote_interface ?? '—'}
                          </td>
                          <td className="px-3 py-2 text-gray-500 max-w-xs truncate">
                            {row.remote_platform ?? '—'}
                          </td>
                          <td className="px-3 py-2 text-gray-500">
                            {relative(row.last_seen)}
                            <span className="ml-1 text-xs uppercase text-gray-400">
                              {row.protocol}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {/* Anything else the probe returned */}
            {Object.keys(data.facts.extra).length > 0 && (
              <details>
                <summary className="text-sm text-blue-600 cursor-pointer">
                  Everything else the probe returned
                </summary>
                <pre className="mt-2 text-xs text-gray-600 whitespace-pre-wrap break-words font-mono bg-gray-50 p-3 rounded max-h-64 overflow-y-auto">
                  {JSON.stringify(data.facts.extra, null, 2)}
                </pre>
              </details>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
