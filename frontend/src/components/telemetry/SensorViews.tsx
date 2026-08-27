/**
 * Charts and tables for what SNMP reports about a device's health.
 *
 * Three forms, picked by what the data has to do:
 *
 * - **Stat tiles** for the current reading per sensor type. A handful of
 *   headline numbers is a KPI row, not a bar chart with one bar per bar.
 * - **A line per sensor** for the trend, because the question is
 *   change-over-time and the series are the subject.
 * - **A table** for the sensors themselves and for the hardware inventory:
 *   past about seven classes that all carry meaning, a table beats more
 *   colours, and a serial number is not a thing to plot.
 *
 * Colours come from the validated categorical palette in fixed slot order -
 * never cycled, never generated. Status is its own reserved set and always
 * ships with an icon and a word, so no meaning is carried by hue alone.
 */
import React, { useMemo } from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { format } from 'date-fns';
import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  Fan,
  Gauge,
  HardDrive,
  MemoryStick,
  Plug,
  Thermometer,
  XCircle,
  Zap,
} from 'lucide-react';
import { DeviceComponentRow, DeviceSensorRow } from '../../types';

/**
 * Categorical slots, in fixed order.
 *
 * A fifth series folds into "and N more" rather than taking a generated hue:
 * a ninth colour is indistinguishable from an existing one under colour-vision
 * deficiency, and cycling breaks the rule that colour follows the entity.
 */
const SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100'];
const MAX_SERIES = SERIES.length;

const INK = { primary: '#0b0b0b', secondary: '#52514e', muted: '#8a8a85' };
const GRID = '#e6e6e2';

export const SENSOR_LABELS: Record<string, string> = {
  temperature: 'Temperature',
  fan: 'Fans',
  voltage: 'Voltage',
  current: 'Current',
  power: 'Power',
  humidity: 'Humidity',
  cpu: 'CPU',
  memory: 'Memory',
  storage: 'Storage',
  airflow: 'Airflow',
  frequency: 'Frequency',
  dbm: 'Optical',
};

const SENSOR_ICONS: Record<
  string,
  React.ComponentType<{ className?: string; style?: React.CSSProperties }>
> = {
  temperature: Thermometer,
  fan: Fan,
  voltage: Zap,
  current: Zap,
  power: Plug,
  cpu: Cpu,
  memory: MemoryStick,
  storage: HardDrive,
};

export function sensorIcon(type: string) {
  return SENSOR_ICONS[type] ?? Gauge;
}

/** A number with its unit, or an em dash when the sensor reports only a state */
export function formatReading(
  value: number | null | undefined,
  unit: string
): string {
  if (value === null || value === undefined) return '—';

  const rounded = Math.abs(value) >= 100 ? Math.round(value) : Math.round(value * 10) / 10;
  return unit ? `${rounded}${unit.startsWith('°') || unit === '%' ? '' : ' '}${unit}` : `${rounded}`;
}

/**
 * A status word with its icon
 *
 * Never colour alone: two of the four status steps sit under 3:1 on a light
 * surface by design, and the icon and word are what carry the meaning.
 */
export const StatusPill: React.FC<{ status: string; compact?: boolean }> = ({
  status,
  compact,
}) => {
  const Icon =
    status === 'ok' ? CheckCircle2 : status === 'warning' ? AlertTriangle : XCircle;

  const tone =
    status === 'ok'
      ? 'bg-emerald-50 text-emerald-800 border-emerald-200'
      : status === 'warning'
      ? 'bg-amber-50 text-amber-900 border-amber-200'
      : status === 'unknown'
      ? 'bg-gray-50 text-gray-700 border-gray-200'
      : 'bg-red-50 text-red-800 border-red-200';

  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs font-medium ${tone}`}
    >
      <Icon className="h-3 w-3" />
      {compact ? null : status}
    </span>
  );
};

// --------------------------------------------------------------------------
// Stat tiles
// --------------------------------------------------------------------------

interface TypeSummary {
  sensor_type: string;
  unit: string;
  count: number;
  min?: number | null;
  avg?: number | null;
  max?: number | null;
  unhealthy?: number;
}

/**
 * One tile per sensor type
 *
 * The headline is the highest reading rather than the average: a chassis with
 * one module at 71°C and eleven at 30°C is not a 33°C chassis, and the maximum
 * is the number somebody acts on.
 */
export const SensorTiles: React.FC<{ summary: TypeSummary[] }> = ({ summary }) => {
  if (!summary.length) return null;

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      {summary.map((entry) => {
        const Icon = sensorIcon(entry.sensor_type);
        const unhealthy = entry.unhealthy ?? 0;
        const headline =
          entry.max ?? entry.avg ?? null;

        return (
          <div
            key={entry.sensor_type}
            data-testid={`sensor-tile-${entry.sensor_type}`}
            className="rounded-lg border border-gray-200 bg-white p-3"
          >
            <div className="flex items-center justify-between">
              <span
                className="inline-flex items-center gap-1.5 text-xs font-medium"
                style={{ color: INK.secondary }}
              >
                <Icon className="h-3.5 w-3.5" />
                {SENSOR_LABELS[entry.sensor_type] ?? entry.sensor_type}
              </span>
              {unhealthy > 0 && <StatusPill status="failed" compact />}
            </div>

            <p
              className="mt-1.5 text-2xl font-semibold tabular-nums"
              style={{ color: INK.primary }}
            >
              {formatReading(headline, entry.unit)}
            </p>

            <p className="text-xs" style={{ color: INK.muted }}>
              {entry.count} sensor{entry.count === 1 ? '' : 's'}
              {entry.min !== null && entry.min !== undefined && entry.max !== null
                ? ` · ${formatReading(entry.min, entry.unit)} to ${formatReading(
                    entry.max,
                    entry.unit
                  )}`
                : ''}
              {unhealthy > 0 ? ` · ${unhealthy} not OK` : ''}
            </p>
          </div>
        );
      })}
    </div>
  );
};

// --------------------------------------------------------------------------
// Trend
// --------------------------------------------------------------------------

interface TrendProps {
  /** Sensors of one type, so the axis has a single unit. */
  sensors: DeviceSensorRow[];
  unit: string;
  title: string;
}

/**
 * One line per sensor, over the history that came back
 *
 * A single axis, always: two measures of different scale get two charts. That
 * is why this takes sensors of one type - plotting °C and RPM together would
 * need a second y-scale, which is the most misread chart there is.
 */
export const SensorTrend: React.FC<TrendProps> = ({ sensors, unit, title }) => {
  const plotted = sensors.filter((sensor) => sensor.history?.length);

  const { data, series, extra } = useMemo(() => {
    const shown = plotted.slice(0, MAX_SERIES);

    // Recharts wants one row per timestamp with a column per series, so the
    // per-sensor series are pivoted here rather than in the API - the API's
    // shape is the one a table wants.
    const byTime = new Map<string, Record<string, number | string>>();

    shown.forEach((sensor) => {
      sensor.history?.forEach((point) => {
        const key = point.at;
        const row = byTime.get(key) ?? { at: key };
        row[`s${sensor.id}`] = point.value ?? NaN;
        byTime.set(key, row);
      });
    });

    return {
      data: [...byTime.values()].sort((a, b) =>
        String(a.at).localeCompare(String(b.at))
      ),
      series: shown,
      extra: plotted.length - shown.length,
    };
  }, [plotted]);

  if (data.length < 2) return null;

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3">
      <div className="flex items-baseline justify-between mb-1">
        <h4 className="text-sm font-medium" style={{ color: INK.primary }}>
          {title}
        </h4>
        <span className="text-xs" style={{ color: INK.muted }}>
          {unit}
          {extra > 0 ? ` · ${extra} more in the table below` : ''}
        </span>
      </div>

      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={data} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="at"
            tick={{ fontSize: 11, fill: INK.muted }}
            tickLine={false}
            axisLine={{ stroke: GRID }}
            minTickGap={40}
            tickFormatter={(value) => {
              try {
                return format(new Date(value), 'HH:mm');
              } catch {
                return value;
              }
            }}
          />
          <YAxis
            tick={{ fontSize: 11, fill: INK.muted }}
            tickLine={false}
            axisLine={false}
            // Fan speeds run to five digits. Recharts clips a tick that does
            // not fit rather than widening the axis, and "000" under a chart
            // of RPM is worse than no axis at all.
            width={56}
            tickFormatter={(value: number) =>
              Math.abs(value) >= 10000
                ? `${(value / 1000).toFixed(0)}k`
                : String(value)
            }
          />
          <Tooltip
            contentStyle={{
              fontSize: 12,
              borderRadius: 6,
              border: `1px solid ${GRID}`,
            }}
            labelFormatter={(value) => {
              try {
                return format(new Date(value as string), 'd MMM HH:mm');
              } catch {
                return String(value);
              }
            }}
            formatter={(value: number, key: string) => {
              const sensor = series.find((entry) => `s${entry.id}` === key);
              return [formatReading(value, unit), sensor?.name ?? key];
            }}
          />
          {series.length > 1 && (
            <Legend
              wrapperStyle={{ fontSize: 11, color: INK.secondary }}
              formatter={(key) =>
                series.find((entry) => `s${entry.id}` === key)?.name ?? key
              }
            />
          )}
          {series.map((sensor, index) => (
            <Line
              key={sensor.id}
              type="monotone"
              dataKey={`s${sensor.id}`}
              name={`s${sensor.id}`}
              stroke={SERIES[index]}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4 }}
              connectNulls
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
};

// --------------------------------------------------------------------------
// Tables
// --------------------------------------------------------------------------

export const SensorTable: React.FC<{ sensors: DeviceSensorRow[] }> = ({ sensors }) => {
  if (!sensors.length) return null;

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase" style={{ color: INK.muted }}>
            <th className="py-2 pr-3 font-medium">Sensor</th>
            <th className="py-2 pr-3 font-medium">Type</th>
            <th className="py-2 pr-3 font-medium text-right">Reading</th>
            <th className="py-2 font-medium">State</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {sensors.map((sensor) => {
            const Icon = sensorIcon(sensor.sensor_type);
            return (
              <tr key={sensor.id}>
                <td className="py-1.5 pr-3" style={{ color: INK.primary }}>
                  <span className="inline-flex items-center gap-1.5">
                    <Icon className="h-3.5 w-3.5" style={{ color: INK.muted }} />
                    {sensor.name}
                  </span>
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
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

const CLASS_LABELS: Record<string, string> = {
  chassis: 'Chassis',
  module: 'Module',
  power: 'Power supply',
  fan: 'Fan',
  stack: 'Stack member',
  cpu: 'CPU',
  port: 'Port',
  sensor: 'Sensor',
  container: 'Slot',
  backplane: 'Backplane',
  other: 'Other',
  unknown: 'Unknown',
};

export const ComponentTable: React.FC<{
  components: DeviceComponentRow[];
  showDevice?: boolean;
}> = ({ components, showDevice }) => {
  if (!components.length) return null;

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase" style={{ color: INK.muted }}>
            {showDevice && <th className="py-2 pr-3 font-medium">Device</th>}
            <th className="py-2 pr-3 font-medium">Part</th>
            <th className="py-2 pr-3 font-medium">Type</th>
            <th className="py-2 pr-3 font-medium">Model</th>
            <th className="py-2 pr-3 font-medium">Serial</th>
            <th className="py-2 font-medium">Revision</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {components.map((component) => (
            <tr key={component.id} className={component.is_active ? '' : 'opacity-60'}>
              {showDevice && (
                <td className="py-1.5 pr-3" style={{ color: INK.secondary }}>
                  {component.device_hostname}
                </td>
              )}
              <td className="py-1.5 pr-3" style={{ color: INK.primary }}>
                {component.name || component.description || '—'}
                {!component.is_active && (
                  <span className="ml-2 text-xs" style={{ color: INK.muted }}>
                    removed
                  </span>
                )}
              </td>
              <td className="py-1.5 pr-3" style={{ color: INK.secondary }}>
                {CLASS_LABELS[component.component_class] ?? component.component_class}
              </td>
              <td className="py-1.5 pr-3" style={{ color: INK.secondary }}>
                {component.model_name || '—'}
              </td>
              <td
                className="py-1.5 pr-3 font-mono text-xs"
                style={{ color: INK.primary }}
              >
                {component.serial_number || '—'}
              </td>
              <td className="py-1.5 text-xs" style={{ color: INK.muted }}>
                {[component.hardware_rev, component.firmware_rev, component.software_rev]
                  .filter(Boolean)
                  .join(' · ') || '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
