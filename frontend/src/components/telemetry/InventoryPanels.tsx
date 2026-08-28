/**
 * Estate-wide views of what SNMP polling collected.
 *
 * The hardware panel is an asset register: every part of every device with its
 * serial number, searchable, because "which chassis is FOC2137L0AB in" is the
 * question a serial number exists to answer. It is a table and not a chart -
 * a serial number is not a thing to plot.
 *
 * The environment panel leads with a KPI row per sensor type and then a bar
 * per device for the type being looked at. Magnitude, low to high, is a bar
 * chart in one hue: the devices are not a categorical set worth eight colours,
 * and the reader's job is "which one is hottest", not "tell these apart".
 */
import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'react-hot-toast';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Cpu, Loader2, RefreshCw, Search } from 'lucide-react';
import api from '../../lib/api';
import { usePermissions } from '../../hooks/usePermissions';
import {
  ComponentTable,
  SENSOR_LABELS,
  StatusPill,
  SensorTiles,
  formatReading,
  sensorIcon,
} from './SensorViews';
import { DeviceComponentRow, PaginatedResponse, SensorOverview } from '../../types';

const INK = { primary: '#0b0b0b', secondary: '#52514e', muted: '#8a8a85' };
const GRID = '#e6e6e2';

/** Sequential blue: magnitude, one hue, more-is-darker where it matters. */
const MAGNITUDE = '#2a78d6';
/** Reserved status steps, for the bars that are not OK. */
const STATUS_CRITICAL = '#d03b3b';
const STATUS_WARNING = '#fab219';

const CLASS_FILTERS = [
  { value: '', label: 'Every part' },
  { value: 'chassis', label: 'Chassis' },
  { value: 'module', label: 'Modules' },
  { value: 'power', label: 'Power supplies' },
  { value: 'fan', label: 'Fans' },
  { value: 'stack', label: 'Stack members' },
];

// --------------------------------------------------------------------------
// Hardware
// --------------------------------------------------------------------------

export const HardwareInventory: React.FC = () => {
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [componentClass, setComponentClass] = useState('');
  const [activeOnly, setActiveOnly] = useState(true);
  const [page, setPage] = useState(1);
  const pageSize = 50;

  const { data, isLoading } = useQuery<PaginatedResponse<DeviceComponentRow>>({
    queryKey: ['components', page, search, componentClass, activeOnly],
    queryFn: async () => {
      const params: Record<string, any> = {
        skip: (page - 1) * pageSize,
        limit: pageSize,
        active_only: activeOnly,
      };
      if (search) params.search = search;
      if (componentClass) params.component_class = componentClass;
      return (await api.get('/devices/components', { params })).data;
    },
  });

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg shadow p-4">
        <div className="flex flex-wrap items-center gap-3">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              setPage(1);
              setSearch(searchInput.trim());
            }}
            className="relative flex-1 min-w-[16rem]"
          >
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
            <input
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="Serial number, model or part name"
              data-testid="hardware-search"
              className="w-full pl-9 pr-3 py-2 border border-gray-300 rounded-lg text-sm"
            />
          </form>

          <select
            value={componentClass}
            onChange={(event) => {
              setPage(1);
              setComponentClass(event.target.value);
            }}
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
          >
            {CLASS_FILTERS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>

          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={activeOnly}
              onChange={(event) => {
                setPage(1);
                setActiveOnly(event.target.checked);
              }}
            />
            Only parts still fitted
          </label>

          <span className="ml-auto text-sm" style={{ color: INK.muted }}>
            {data ? `${data.total.toLocaleString()} parts` : ''}
          </span>
        </div>
      </div>

      <div className="bg-white rounded-lg shadow p-4">
        {isLoading ? (
          <div className="p-8 flex justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
          </div>
        ) : !data?.items.length ? (
          <div className="p-8 text-center text-sm" style={{ color: INK.secondary }}>
            <p className="font-medium" style={{ color: INK.primary }}>
              No hardware recorded yet
            </p>
            <p className="mt-1">
              This comes from SNMP. Give devices a community, or point them at
              one in the credential vault, then poll from the Devices page.
            </p>
          </div>
        ) : (
          <>
            <ComponentTable components={data.items} showDevice />

            {data.total_pages > 1 && (
              <div className="flex items-center justify-between pt-3 mt-3 border-t border-gray-100 text-sm">
                <span style={{ color: INK.secondary }}>
                  Page {data.page} of {data.total_pages}
                </span>
                <div className="flex gap-2">
                  <button
                    onClick={() => setPage((current) => Math.max(1, current - 1))}
                    disabled={page <= 1}
                    className="px-3 py-1.5 border border-gray-300 rounded disabled:opacity-50"
                  >
                    Previous
                  </button>
                  <button
                    onClick={() => setPage((current) => current + 1)}
                    disabled={page >= data.total_pages}
                    className="px-3 py-1.5 border border-gray-300 rounded disabled:opacity-50"
                  >
                    Next
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};

// --------------------------------------------------------------------------
// Environment
// --------------------------------------------------------------------------

export const EnvironmentOverview: React.FC = () => {
  const queryClient = useQueryClient();
  const { can } = usePermissions();
  const canPoll = can('discovery:run');

  const [selectedType, setSelectedType] = useState<string | null>(null);

  const { data, isLoading } = useQuery<SensorOverview>({
    queryKey: ['sensors-overview'],
    queryFn: async () => (await api.get('/devices/sensors')).data,
  });

  const pollMutation = useMutation({
    mutationFn: async () => (await api.post('/devices/poll-telemetry', {})).data,
    onSuccess: (result) => {
      toast.success(result.message || 'Polling queued');
      // Results arrive as devices answer.
      setTimeout(
        () => queryClient.invalidateQueries({ queryKey: ['sensors-overview'] }),
        5000
      );
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.detail || 'Could not queue the poll');
    },
  });

  const summary = data?.summary ?? [];
  const sensors = data?.items ?? [];

  // The type being charted: whichever was picked, else the first that has
  // numbers to draw.
  const charted =
    selectedType ?? summary.find((entry) => entry.max !== null)?.sensor_type ?? null;

  const chartData = useMemo(() => {
    if (!charted) return [];

    // One bar per device, at that device's highest reading of this type. The
    // maximum rather than the average: a chassis with one module at 71°C is
    // not a 33°C chassis, and the hottest part is what somebody acts on.
    const highest = new Map<string, { device: string; value: number; status: string }>();

    sensors
      .filter(
        (sensor) => sensor.sensor_type === charted && sensor.value !== null
      )
      .forEach((sensor) => {
        const device = sensor.device_hostname ?? `Device ${sensor.device_id}`;
        const current = highest.get(device);
        if (!current || (sensor.value as number) > current.value) {
          highest.set(device, {
            device,
            value: sensor.value as number,
            status: sensor.status,
          });
        }
      });

    return [...highest.values()].sort((a, b) => b.value - a.value).slice(0, 20);
  }, [charted, sensors]);

  const unit = summary.find((entry) => entry.sensor_type === charted)?.unit ?? '';

  const unhealthy = sensors.filter((sensor) => sensor.status !== 'ok');

  if (isLoading) {
    return (
      <div className="bg-white rounded-lg shadow p-8 flex justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-blue-600" />
      </div>
    );
  }

  if (!summary.length) {
    return (
      <div className="bg-white rounded-lg shadow p-8 text-center text-sm">
        <Cpu className="h-10 w-10 mx-auto mb-3 text-gray-300" />
        <p className="font-medium" style={{ color: INK.primary }}>
          Nothing polled yet
        </p>
        <p className="mt-1" style={{ color: INK.secondary }}>
          Temperature, fans, power, CPU and memory come from SNMP. Give devices
          a community, or point them at one in the credential vault.
        </p>
        {canPoll && (
          <button
            onClick={() => pollMutation.mutate()}
            disabled={pollMutation.isPending}
            data-testid="poll-estate"
            className="mt-4 inline-flex items-center px-3 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50"
          >
            <RefreshCw
              className={`h-4 w-4 mr-2 ${pollMutation.isPending ? 'animate-spin' : ''}`}
            />
            Poll every device now
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase" style={{ color: INK.muted }}>
          Across every polled device
        </h2>
        {canPoll && (
          <button
            onClick={() => pollMutation.mutate()}
            disabled={pollMutation.isPending}
            data-testid="poll-estate"
            className="inline-flex items-center px-3 py-1.5 border border-gray-300 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50"
          >
            <RefreshCw
              className={`h-4 w-4 mr-2 ${pollMutation.isPending ? 'animate-spin' : ''}`}
            />
            Poll now
          </button>
        )}
      </div>

      <SensorTiles summary={summary} />

      {/* Anything not reporting OK, first: it is the reason to look at all */}
      {unhealthy.length > 0 && (
        <div className="bg-white rounded-lg shadow p-4">
          <h3 className="text-sm font-medium mb-2" style={{ color: INK.primary }}>
            {unhealthy.length} sensor{unhealthy.length === 1 ? '' : 's'} not
            reporting OK
          </h3>
          <ul className="space-y-1 text-sm">
            {unhealthy.slice(0, 12).map((sensor) => {
              const Icon = sensorIcon(sensor.sensor_type);
              return (
                <li key={sensor.id} className="flex items-center gap-2">
                  <StatusPill status={sensor.status} />
                  <Icon className="h-3.5 w-3.5" style={{ color: INK.muted }} />
                  <span style={{ color: INK.primary }}>{sensor.name}</span>
                  <span style={{ color: INK.muted }}>
                    on {sensor.device_hostname}
                  </span>
                  <span className="ml-auto tabular-nums" style={{ color: INK.secondary }}>
                    {formatReading(sensor.value, sensor.unit)}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {/* Magnitude across devices: one hue, sorted, direct-labelled */}
      {chartData.length > 0 && (
        <div className="bg-white rounded-lg shadow p-4">
          <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
            <h3 className="text-sm font-medium" style={{ color: INK.primary }}>
              {SENSOR_LABELS[charted!] ?? charted!} — highest per device
            </h3>
            <div className="flex flex-wrap gap-1.5">
              {summary
                .filter((entry) => entry.max !== null)
                .map((entry) => (
                  <button
                    key={entry.sensor_type}
                    onClick={() => setSelectedType(entry.sensor_type)}
                    data-testid={`chart-type-${entry.sensor_type}`}
                    className={`px-2.5 py-1 rounded-full text-xs border ${
                      charted === entry.sensor_type
                        ? 'border-blue-300 bg-blue-50 text-blue-800'
                        : 'border-gray-300 text-gray-600 hover:bg-gray-50'
                    }`}
                  >
                    {SENSOR_LABELS[entry.sensor_type] ?? entry.sensor_type}
                  </button>
                ))}
            </div>
          </div>

          <ResponsiveContainer width="100%" height={Math.max(160, chartData.length * 30)}>
            <BarChart
              data={chartData}
              layout="vertical"
              margin={{ top: 4, right: 56, bottom: 4, left: 8 }}
            >
              <CartesianGrid stroke={GRID} strokeDasharray="3 3" horizontal={false} />
              <XAxis
                type="number"
                tick={{ fontSize: 11, fill: INK.muted }}
                tickLine={false}
                axisLine={{ stroke: GRID }}
              />
              <YAxis
                type="category"
                dataKey="device"
                tick={{ fontSize: 11, fill: INK.secondary }}
                tickLine={false}
                axisLine={false}
                width={130}
              />
              <Tooltip
                cursor={{ fill: 'rgba(15,23,42,0.04)' }}
                contentStyle={{
                  fontSize: 12,
                  borderRadius: 6,
                  border: `1px solid ${GRID}`,
                }}
                formatter={(value: number) => [
                  formatReading(value, unit),
                  SENSOR_LABELS[charted!] ?? charted!,
                ]}
              />
              <Bar dataKey="value" radius={[0, 4, 4, 0]} barSize={16}>
                {chartData.map((entry) => (
                  <Cell
                    key={entry.device}
                    // Status is reserved and wins over magnitude: a failing
                    // sensor is not just a taller bar.
                    fill={
                      entry.status === 'ok'
                        ? MAGNITUDE
                        : entry.status === 'warning'
                        ? STATUS_WARNING
                        : STATUS_CRITICAL
                    }
                  />
                ))}
                <LabelList
                  dataKey="value"
                  position="right"
                  formatter={(value: number) => formatReading(value, unit)}
                  style={{ fontSize: 11, fill: INK.secondary }}
                />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* The table view, which the contrast rule obliges and which is the
          honest answer for more classes than a chart can carry */}
      <div className="bg-white rounded-lg shadow p-4">
        <h3 className="text-sm font-medium mb-2" style={{ color: INK.primary }}>
          Every reading
        </h3>
        <div className="overflow-x-auto max-h-[28rem] overflow-y-auto">
          <table className="min-w-full text-sm">
            <thead className="sticky top-0 bg-white">
              <tr className="text-left text-xs uppercase" style={{ color: INK.muted }}>
                <th className="py-2 pr-3 font-medium">Device</th>
                <th className="py-2 pr-3 font-medium">Sensor</th>
                <th className="py-2 pr-3 font-medium">Type</th>
                <th className="py-2 pr-3 font-medium text-right">Reading</th>
                <th className="py-2 font-medium">State</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {sensors.map((sensor) => (
                <tr key={sensor.id}>
                  <td className="py-1.5 pr-3" style={{ color: INK.secondary }}>
                    {sensor.device_hostname}
                  </td>
                  <td className="py-1.5 pr-3" style={{ color: INK.primary }}>
                    {sensor.name}
                  </td>
                  <td className="py-1.5 pr-3" style={{ color: INK.secondary }}>
                    {SENSOR_LABELS[sensor.sensor_type] ?? sensor.sensor_type}
                  </td>
                  <td
                    className="py-1.5 pr-3 text-right tabular-nums"
                    style={{ color: INK.primary }}
                  >
                    {formatReading(sensor.value, sensor.unit)}
                  </td>
                  <td className="py-1.5">
                    <StatusPill status={sensor.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
