/**
 * Device filter editor for a scheduled backup job
 *
 * Every criterion is ANDed; a list within one criterion is ORed. Leaving
 * everything blank means "every device that can be backed up", which is what
 * a job created before filtering existed does.
 *
 * The match count comes from the API rather than being computed here, so what
 * the editor shows is exactly what the job will select at 2am. It is debounced
 * because it fires on every keystroke in the hostname pattern.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronRight,
  Loader2,
  Plus,
  Server,
  X,
} from 'lucide-react';
import api from '../../lib/api';
import { DeviceFilter, DeviceFilterPreview, FilterOptions } from '../../types';

interface Props {
  value: DeviceFilter;
  onChange: (next: DeviceFilter) => void;
}

/**
 * One line describing what a filter selects
 *
 * Mirrors describe() on the backend, so a job's scope is readable in the list
 * without a request per row.
 */
export function describeFilter(filter?: DeviceFilter | null): string {
  const pruned = pruneFilter(filter ?? {});
  const parts: string[] = [];

  if (pruned.device_ids?.length)
    parts.push(`${pruned.device_ids.length} named device(s)`);
  if (pruned.device_types?.length)
    parts.push(pruned.device_types.join(' or '));
  if (pruned.locations?.length) parts.push(`in ${pruned.locations.join(' or ')}`);
  if (pruned.hostname_pattern) parts.push(`named ${pruned.hostname_pattern}`);
  if (pruned.tags)
    parts.push(
      Object.entries(pruned.tags)
        .map(([key, value]) => `${key}=${value}`)
        .join(' + ')
    );
  if (pruned.transports?.length) parts.push(`over ${pruned.transports.join('/')}`);
  if (pruned.exclude_device_ids?.length)
    parts.push(`less ${pruned.exclude_device_ids.length}`);
  if (pruned.include_inactive) parts.push('including inactive');

  return parts.length ? parts.join(', ') : 'All backable devices';
}

/** Strip empty criteria, so `{}` is sent rather than a bag of blanks */
export function pruneFilter(filter: DeviceFilter): DeviceFilter {
  const pruned: DeviceFilter = {};

  if (filter.device_ids?.length) pruned.device_ids = filter.device_ids;
  if (filter.exclude_device_ids?.length)
    pruned.exclude_device_ids = filter.exclude_device_ids;
  if (filter.device_types?.length) pruned.device_types = filter.device_types;
  if (filter.locations?.length) pruned.locations = filter.locations;
  if (filter.transports?.length) pruned.transports = filter.transports;
  if (filter.hostname_pattern?.trim())
    pruned.hostname_pattern = filter.hostname_pattern.trim();
  if (filter.tags && Object.keys(filter.tags).length) pruned.tags = filter.tags;
  if (filter.include_inactive) pruned.include_inactive = true;
  if (filter.include_snmp) pruned.include_snmp = true;

  return pruned;
}

const Chip: React.FC<{
  label: string;
  selected: boolean;
  onClick: () => void;
}> = ({ label, selected, onClick }) => (
  <button
    type="button"
    onClick={onClick}
    className={`px-2.5 py-1 rounded text-xs font-medium transition ${
      selected
        ? 'bg-blue-600 text-white'
        : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
    }`}
  >
    {label}
  </button>
);

export const DeviceFilterEditor: React.FC<Props> = ({ value, onChange }) => {
  const [expanded, setExpanded] = useState(
    Object.keys(pruneFilter(value)).length > 0
  );
  const [debounced, setDebounced] = useState<DeviceFilter>(value);
  const [tagKey, setTagKey] = useState('');
  const [tagValue, setTagValue] = useState('');

  const { data: options } = useQuery<FilterOptions>({
    queryKey: ['job-filter-options'],
    queryFn: async () => (await api.get('/backup-jobs/filter-options')).data,
    staleTime: 5 * 60 * 1000,
  });

  // Debounce what gets previewed, so typing a pattern is not one request per
  // keystroke.
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), 400);
    return () => clearTimeout(timer);
  }, [value]);

  const pruned = useMemo(() => pruneFilter(debounced), [debounced]);

  const { data: preview, isFetching } = useQuery<DeviceFilterPreview>({
    queryKey: ['job-filter-preview', pruned],
    queryFn: async () =>
      (
        await api.post('/backup-jobs/preview-filter', {
          device_filter: pruned,
          limit: 25,
        })
      ).data,
    // A rejected filter is shown as an error line, not a toast storm.
    retry: false,
  });

  const toggleIn = (key: 'device_types' | 'locations' | 'transports', entry: string) => {
    const current = value[key] ?? [];
    const next = current.includes(entry)
      ? current.filter((item) => item !== entry)
      : [...current, entry];
    onChange({ ...value, [key]: next });
  };

  const addTag = () => {
    if (!tagKey.trim()) return;
    onChange({
      ...value,
      tags: { ...(value.tags ?? {}), [tagKey.trim()]: tagValue.trim() },
    });
    setTagKey('');
    setTagValue('');
  };

  const removeTag = (key: string) => {
    const tags = { ...(value.tags ?? {}) };
    delete tags[key];
    onChange({ ...value, tags });
  };

  const criteriaCount = Object.keys(pruneFilter(value)).length;

  return (
    <div className="border border-gray-200 rounded-lg">
      <button
        type="button"
        onClick={() => setExpanded((current) => !current)}
        className="w-full flex items-center justify-between px-4 py-3 text-left"
      >
        <span className="flex items-center text-sm font-medium text-gray-900">
          {expanded ? (
            <ChevronDown className="h-4 w-4 mr-2 text-gray-400" />
          ) : (
            <ChevronRight className="h-4 w-4 mr-2 text-gray-400" />
          )}
          Which devices
        </span>

        <span className="flex items-center gap-3 text-xs">
          {criteriaCount === 0 ? (
            <span className="text-gray-500">Every device that can be backed up</span>
          ) : (
            <span className="text-gray-600">
              {criteriaCount} criteri{criteriaCount === 1 ? 'on' : 'a'}
            </span>
          )}

          <span className="inline-flex items-center px-2 py-0.5 rounded bg-blue-50 text-blue-700 font-medium">
            {isFetching ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <>
                <Server className="h-3 w-3 mr-1" />
                {preview?.total ?? 0} device{preview?.total === 1 ? '' : 's'}
              </>
            )}
          </span>
        </span>
      </button>

      {expanded && (
        <div className="px-4 pb-4 space-y-4 border-t border-gray-100 pt-4">
          <p className="text-xs text-gray-500">
            Criteria are combined with AND; values within one criterion with OR.
            Leave everything blank to cover every device.
          </p>

          {/* Device types */}
          {!!options?.device_types.length && (
            <div>
              <label className="block text-xs font-semibold text-gray-500 uppercase mb-2">
                Device type
              </label>
              <div className="flex flex-wrap gap-1.5">
                {options.device_types.map((entry) => (
                  <Chip
                    key={entry}
                    label={entry}
                    selected={(value.device_types ?? []).includes(entry)}
                    onClick={() => toggleIn('device_types', entry)}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Locations */}
          {!!options?.locations.length && (
            <div>
              <label className="block text-xs font-semibold text-gray-500 uppercase mb-2">
                Location
              </label>
              <div className="flex flex-wrap gap-1.5">
                {options.locations.map((entry) => (
                  <Chip
                    key={entry}
                    label={entry}
                    selected={(value.locations ?? []).includes(entry)}
                    onClick={() => toggleIn('locations', entry)}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Hostname pattern */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase mb-2">
              Hostname pattern
            </label>
            <input
              value={value.hostname_pattern ?? ''}
              onChange={(event) =>
                onChange({ ...value, hostname_pattern: event.target.value })
              }
              placeholder="core-*"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono"
            />
            <p className="text-xs text-gray-500 mt-1">
              <code>*</code> matches any run of characters, <code>?</code> exactly
              one.
            </p>
          </div>

          {/* Tags */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase mb-2">
              Tags
            </label>

            {!!Object.keys(value.tags ?? {}).length && (
              <div className="flex flex-wrap gap-1.5 mb-2">
                {Object.entries(value.tags ?? {}).map(([key, tag]) => (
                  <span
                    key={key}
                    className="inline-flex items-center px-2 py-1 bg-blue-50 text-blue-800 rounded text-xs"
                  >
                    {key}={String(tag)}
                    <button
                      type="button"
                      onClick={() => removeTag(key)}
                      className="ml-1.5 hover:text-blue-950"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}

            <div className="flex gap-2">
              <input
                value={tagKey}
                onChange={(event) => setTagKey(event.target.value)}
                placeholder="key"
                list="job-filter-tag-keys"
                className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm"
              />
              <datalist id="job-filter-tag-keys">
                {options?.tag_keys.map((key) => (
                  <option key={key} value={key} />
                ))}
              </datalist>

              <input
                value={tagValue}
                onChange={(event) => setTagValue(event.target.value)}
                placeholder="value"
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    addTag();
                  }
                }}
                className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm"
              />

              <button
                type="button"
                onClick={addTag}
                disabled={!tagKey.trim()}
                className="px-3 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50"
              >
                <Plus className="h-4 w-4" />
              </button>
            </div>
            <p className="text-xs text-gray-500 mt-1">
              A device must carry every pair listed.
            </p>
          </div>

          {/* Transports and flags */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase mb-2">
              Transport
            </label>
            <div className="flex flex-wrap gap-1.5">
              {(options?.transports ?? ['ssh', 'telnet', 'snmp']).map((entry) => (
                <Chip
                  key={entry}
                  label={entry}
                  selected={(value.transports ?? []).includes(entry)}
                  onClick={() => toggleIn('transports', entry)}
                />
              ))}
            </div>
          </div>

          <div className="space-y-2 text-sm">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={value.include_inactive ?? false}
                onChange={(event) =>
                  onChange({ ...value, include_inactive: event.target.checked })
                }
              />
              Include devices marked inactive
            </label>

            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={value.include_snmp ?? false}
                onChange={(event) =>
                  onChange({ ...value, include_snmp: event.target.checked })
                }
              />
              Include SNMP-only devices
            </label>
            <p className="text-xs text-amber-700 ml-6">
              SNMP cannot retrieve a configuration, so those devices fail on every
              run. They are excluded unless you ask for them.
            </p>
          </div>

          {/* What it selects */}
          <div className="border-t border-gray-100 pt-3">
            {preview ? (
              <>
                <p className="text-xs text-gray-600 mb-2">
                  {preview.summary} —{' '}
                  <strong>
                    {preview.total} device{preview.total === 1 ? '' : 's'}
                  </strong>
                </p>

                {preview.total === 0 ? (
                  <p className="flex items-center text-xs text-amber-700">
                    <AlertTriangle className="h-3.5 w-3.5 mr-1.5" />
                    Nothing matches, so this job will do nothing when it runs.
                  </p>
                ) : (
                  <ul className="flex flex-wrap gap-1.5">
                    {preview.devices.map((device) => (
                      <li
                        key={device.id}
                        className="inline-flex items-center px-2 py-0.5 bg-gray-100 rounded text-xs text-gray-700"
                        title={`${device.ip_address} · ${device.device_type}${
                          device.location ? ` · ${device.location}` : ''
                        }`}
                      >
                        <Check className="h-3 w-3 mr-1 text-emerald-600" />
                        {device.hostname}
                      </li>
                    ))}
                    {preview.truncated && (
                      <li className="text-xs text-gray-500 self-center">
                        and {preview.total - preview.devices.length} more
                      </li>
                    )}
                  </ul>
                )}
              </>
            ) : (
              <p className="flex items-center text-xs text-red-700">
                <AlertTriangle className="h-3.5 w-3.5 mr-1.5" />
                This filter was rejected; check the criteria above.
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
