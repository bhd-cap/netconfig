/**
 * Reports Page
 *
 * Reporting on connected devices: headline counts, the vendor breakdown from
 * the OUI table, what each switch port carries, and what appeared or
 * disappeared over a window.
 */
import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { format, formatDistanceToNow } from 'date-fns';
import {
  BarChart,
  Bar,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  ArrowDownRight,
  ArrowUpRight,
  Download,
  Loader2,
  Network,
  Server,
  Tag,
  Users,
} from 'lucide-react';
import api from '../lib/api';
import {
  ChangeReport,
  InventorySummary,
  PortReport,
  VendorReport,
} from '../types';

const VENDOR_COLOURS = [
  '#2563eb',
  '#10b981',
  '#f59e0b',
  '#8b5cf6',
  '#ef4444',
  '#06b6d4',
  '#ec4899',
  '#84cc16',
];

function absolute(value?: string | null): string {
  if (!value) return '—';
  try {
    return format(new Date(value), 'd MMM yyyy HH:mm');
  } catch {
    return value;
  }
}

function relative(value?: string | null): string {
  if (!value) return '—';
  try {
    return formatDistanceToNow(new Date(value), { addSuffix: true });
  } catch {
    return value;
  }
}

interface StatCardProps {
  label: string;
  value: string | number;
  hint?: string;
  icon: React.ElementType;
  tone?: 'blue' | 'green' | 'amber' | 'slate';
}

const StatCard: React.FC<StatCardProps> = ({
  label,
  value,
  hint,
  icon: Icon,
  tone = 'blue',
}) => {
  const tones = {
    blue: 'bg-blue-50 text-blue-600',
    green: 'bg-emerald-50 text-emerald-600',
    amber: 'bg-amber-50 text-amber-600',
    slate: 'bg-slate-100 text-slate-600',
  };

  return (
    <div className="bg-white rounded-lg shadow p-5">
      <div className="flex items-start justify-between">
        <div className="min-w-0">
          <p className="text-sm text-gray-500">{label}</p>
          <p className="text-2xl font-bold text-gray-900 mt-1">{value}</p>
          {hint && <p className="text-xs text-gray-500 mt-1">{hint}</p>}
        </div>
        <div className={`p-2 rounded-lg shrink-0 ${tones[tone]}`}>
          <Icon className="h-5 w-5" />
        </div>
      </div>
    </div>
  );
};

export const Reports: React.FC = () => {
  const [changeDays, setChangeDays] = useState(7);
  const [minHosts, setMinHosts] = useState(1);

  const { data: summary, isLoading } = useQuery<InventorySummary>({
    queryKey: ['report-summary'],
    queryFn: async () => (await api.get('/inventory/reports/summary')).data,
  });

  const { data: vendors } = useQuery<VendorReport>({
    queryKey: ['report-vendors'],
    queryFn: async () =>
      (await api.get('/inventory/reports/by-vendor', { params: { limit: 12 } })).data,
  });

  const { data: ports } = useQuery<PortReport>({
    queryKey: ['report-ports', minHosts],
    queryFn: async () =>
      (
        await api.get('/inventory/reports/by-port', {
          params: { min_hosts: minHosts, limit: 200 },
        })
      ).data,
  });

  const { data: changes } = useQuery<ChangeReport>({
    queryKey: ['report-changes', changeDays],
    queryFn: async () =>
      (await api.get('/inventory/reports/changes', { params: { days: changeDays } }))
        .data,
  });

  const exportCsv = async () => {
    const response = await api.get('/inventory/reports/export', {
      responseType: 'blob',
    });
    const url = URL.createObjectURL(new Blob([response.data]));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `connected_devices_${new Date()
      .toISOString()
      .slice(0, 10)}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Reports</h1>
          <p className="text-gray-600">Connected devices across the network.</p>
        </div>

        <button
          onClick={exportCsv}
          className="inline-flex items-center px-3 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50"
        >
          <Download className="h-4 w-4 mr-2" />
          Export CSV
        </button>
      </div>

      {/* Headline counts */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="Hosts currently seen"
          value={(summary?.active_entries ?? 0).toLocaleString()}
          hint={`${(summary?.unique_macs ?? 0).toLocaleString()} distinct MAC addresses`}
          icon={Users}
          tone="green"
        />
        <StatCard
          label="Switches reporting"
          value={summary?.switches_reporting ?? 0}
          hint={`${(summary?.total_entries ?? 0).toLocaleString()} entries in total`}
          icon={Server}
        />
        <StatCard
          label="New in the last 24h"
          value={summary?.new_last_24h ?? 0}
          hint={`${summary?.seen_last_24h ?? 0} seen in that window`}
          icon={Network}
          tone="amber"
        />
        <StatCard
          label="Unknown vendor"
          value={summary?.unknown_vendor ?? 0}
          hint="Import more OUI data to resolve these"
          icon={Tag}
          tone="slate"
        />
      </div>

      {/* Vendors */}
      <div className="bg-white rounded-lg shadow p-6">
        <h2 className="text-lg font-semibold text-gray-900 mb-1">By vendor</h2>
        <p className="text-sm text-gray-500 mb-4">
          Resolved from the first three octets of each MAC against the public
          OUI registry.
        </p>

        {!vendors?.vendors.length ? (
          <p className="text-sm text-gray-500 py-8 text-center">
            Nothing to report yet.
          </p>
        ) : (
          <div className="h-80">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={vendors.vendors}
                layout="vertical"
                margin={{ left: 24, right: 24 }}
              >
                <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                <XAxis type="number" allowDecimals={false} />
                <YAxis
                  type="category"
                  dataKey="vendor"
                  width={160}
                  tick={{ fontSize: 12 }}
                />
                <Tooltip />
                <Bar dataKey="hosts" radius={[0, 4, 4, 0]}>
                  {vendors.vendors.map((entry, index) => (
                    <Cell
                      key={entry.vendor}
                      fill={
                        entry.vendor === 'Unknown'
                          ? '#cbd5e1'
                          : VENDOR_COLOURS[index % VENDOR_COLOURS.length]
                      }
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {/* Ports */}
      <div className="bg-white rounded-lg shadow">
        <div className="px-6 py-4 border-b border-gray-200 flex flex-wrap items-center gap-4">
          <div className="mr-auto">
            <h2 className="text-lg font-semibold text-gray-900">By switch port</h2>
            <p className="text-sm text-gray-500">
              A port carrying many MACs is usually an uplink or a downstream
              unmanaged switch.
            </p>
          </div>

          <label className="text-sm text-gray-600 flex items-center gap-2">
            At least
            <input
              type="number"
              min={1}
              value={minHosts}
              onChange={(event) =>
                setMinHosts(Math.max(1, Number(event.target.value)))
              }
              className="w-20 px-2 py-1 border border-gray-300 rounded"
            />
            host(s)
          </label>
        </div>

        {!ports?.ports.length ? (
          <p className="p-8 text-sm text-gray-500 text-center">
            No ports match that filter.
          </p>
        ) : (
          <div className="overflow-x-auto max-h-96 overflow-y-auto">
            <table className="min-w-full divide-y divide-gray-200 text-sm">
              <thead className="bg-gray-50 sticky top-0">
                <tr>
                  <th className="px-6 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                    Switch
                  </th>
                  <th className="px-6 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                    Port
                  </th>
                  <th className="px-6 py-3 text-right font-medium text-gray-500 uppercase text-xs">
                    Hosts
                  </th>
                  <th className="px-6 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                    First seen
                  </th>
                  <th className="px-6 py-3 text-left font-medium text-gray-500 uppercase text-xs">
                    Last seen
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {ports.ports.map((port) => (
                  <tr key={`${port.device_id}-${port.interface}`}>
                    <td className="px-6 py-3 font-medium text-gray-900">
                      {port.device_hostname}
                    </td>
                    <td className="px-6 py-3 text-gray-700">
                      {port.interface}
                      {port.likely_uplink && (
                        <span className="ml-2 px-1.5 py-0.5 bg-amber-100 text-amber-800 rounded text-xs">
                          likely uplink
                        </span>
                      )}
                    </td>
                    <td className="px-6 py-3 text-right text-gray-900">
                      {port.hosts}
                    </td>
                    <td className="px-6 py-3 text-gray-500">
                      {absolute(port.first_seen)}
                    </td>
                    <td className="px-6 py-3 text-gray-500">
                      {relative(port.last_seen)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Changes */}
      <div className="bg-white rounded-lg shadow">
        <div className="px-6 py-4 border-b border-gray-200 flex flex-wrap items-center gap-4">
          <div className="mr-auto">
            <h2 className="text-lg font-semibold text-gray-900">What changed</h2>
            <p className="text-sm text-gray-500">
              Hosts that turned up, and hosts that stopped being seen.
            </p>
          </div>

          <select
            value={changeDays}
            onChange={(event) => setChangeDays(Number(event.target.value))}
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
          >
            <option value={1}>Last day</option>
            <option value={7}>Last week</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
          </select>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 divide-y lg:divide-y-0 lg:divide-x divide-gray-200">
          <div className="p-6">
            <h3 className="flex items-center text-sm font-semibold text-emerald-700 mb-3">
              <ArrowUpRight className="h-4 w-4 mr-1" />
              Appeared ({changes?.appeared_count ?? 0})
            </h3>

            {!changes?.appeared.length ? (
              <p className="text-sm text-gray-500">Nothing new in this window.</p>
            ) : (
              <ul className="space-y-2 max-h-80 overflow-y-auto">
                {changes.appeared.map((entry) => (
                  <li
                    key={`${entry.mac_address}-${entry.device_hostname}-${entry.interface}`}
                    className="text-sm border border-gray-100 rounded p-2"
                  >
                    <p className="font-mono text-xs text-gray-900">
                      {entry.mac_address}
                    </p>
                    <p className="text-gray-600">
                      {entry.vendor || 'unknown vendor'}
                      {entry.ip_address ? ` · ${entry.ip_address}` : ''}
                    </p>
                    <p className="text-xs text-gray-500">
                      {entry.device_hostname} {entry.interface} ·{' '}
                      {relative(entry.first_seen)}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="p-6">
            <h3 className="flex items-center text-sm font-semibold text-gray-700 mb-3">
              <ArrowDownRight className="h-4 w-4 mr-1" />
              Stopped being seen ({changes?.disappeared_count ?? 0})
            </h3>

            {!changes?.disappeared.length ? (
              <p className="text-sm text-gray-500">Nothing has gone missing.</p>
            ) : (
              <ul className="space-y-2 max-h-80 overflow-y-auto">
                {changes.disappeared.map((entry) => (
                  <li
                    key={`${entry.mac_address}-${entry.device_hostname}-${entry.interface}`}
                    className="text-sm border border-gray-100 rounded p-2"
                  >
                    <p className="font-mono text-xs text-gray-900">
                      {entry.mac_address}
                    </p>
                    <p className="text-gray-600">
                      {entry.vendor || 'unknown vendor'}
                      {entry.ip_address ? ` · ${entry.ip_address}` : ''}
                    </p>
                    <p className="text-xs text-gray-500">
                      last on {entry.device_hostname} {entry.interface} ·{' '}
                      {relative(entry.last_seen)}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
