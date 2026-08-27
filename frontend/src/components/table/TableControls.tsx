/**
 * Shared table controls: a sortable column header and a page-size selector.
 *
 * Both the device list and the host inventory need the same two things, and
 * the sort arrow has to mean the same thing on both - a header that looks
 * sortable but is inert, or two pages that disagree about which arrow is
 * ascending, is worse than no control at all.
 */
import React from 'react';
import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react';

export type SortDir = 'asc' | 'desc';

export interface SortableColumn {
  key: string;
  label: string;
  /** Right-align numeric or date columns, as the body cells are. */
  align?: 'left' | 'right';
}

interface SortHeaderProps {
  column: SortableColumn;
  sortBy: string;
  sortDir: SortDir;
  onSort: (key: string) => void;
}

/**
 * A column header that sorts on click
 *
 * The arrow shows the current direction on the active column and a neutral
 * up/down on the others, so which column is driving the order is visible
 * without clicking anything.
 */
export const SortHeader: React.FC<SortHeaderProps> = ({
  column,
  sortBy,
  sortDir,
  onSort,
}) => {
  const active = sortBy === column.key;
  const Icon = !active ? ArrowUpDown : sortDir === 'asc' ? ArrowUp : ArrowDown;

  return (
    <th
      className={`px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider ${
        column.align === 'right' ? 'text-right' : 'text-left'
      }`}
      aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
    >
      <button
        type="button"
        onClick={() => onSort(column.key)}
        data-testid={`sort-${column.key}`}
        className={`inline-flex items-center gap-1 hover:text-gray-900 transition ${
          active ? 'text-gray-900' : ''
        }`}
        title={
          active
            ? `Sorted ${sortDir === 'asc' ? 'ascending' : 'descending'}; click to reverse`
            : `Sort by ${column.label}`
        }
      >
        {column.label}
        <Icon className={`h-3 w-3 ${active ? 'text-blue-600' : 'text-gray-400'}`} />
      </button>
    </th>
  );
};

/**
 * Toggle logic shared by every sortable table
 *
 * Clicking the active column reverses it; clicking a new one starts ascending.
 * Returns the next [sortBy, sortDir] rather than setting state, so the caller
 * decides what else a sort change resets - the page number, usually.
 */
export function nextSort(
  key: string,
  sortBy: string,
  sortDir: SortDir
): [string, SortDir] {
  if (key === sortBy) return [key, sortDir === 'asc' ? 'desc' : 'asc'];
  return [key, 'asc'];
}

export const PAGE_SIZES = [10, 20, 50, 100] as const;

interface PageSizeSelectProps {
  value: number;
  onChange: (size: number) => void;
  /** What is being counted, for the label: "devices", "hosts". */
  noun?: string;
}

export const PageSizeSelect: React.FC<PageSizeSelectProps> = ({
  value,
  onChange,
  noun = 'rows',
}) => (
  <label className="inline-flex items-center gap-2 text-sm text-gray-600">
    <span>Show</span>
    <select
      value={value}
      onChange={(event) => onChange(Number(event.target.value))}
      data-testid="page-size"
      aria-label={`${noun} per page`}
      className="border border-gray-300 rounded-lg px-2 py-1 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
    >
      {PAGE_SIZES.map((size) => (
        <option key={size} value={size}>
          {size}
        </option>
      ))}
    </select>
    <span>{noun} per page</span>
  </label>
);

/**
 * The page numbers worth drawing, with gaps marked
 *
 * One button per page is fine for five pages and unusable for eighty, which is
 * what a small page size over a discovered estate produces. First and last are
 * always shown, along with a span either side of the current page; everything
 * else collapses to a gap.
 */
export function pageWindow(
  current: number,
  total: number,
  span = 1
): Array<number | 'gap'> {
  if (total <= 7) {
    return Array.from({ length: Math.max(total, 1) }, (_, index) => index + 1);
  }

  const wanted = new Set<number>([1, total, current]);
  for (let offset = 1; offset <= span; offset += 1) {
    if (current - offset > 1) wanted.add(current - offset);
    if (current + offset < total) wanted.add(current + offset);
  }

  const pages = [...wanted].sort((a, b) => a - b);
  const out: Array<number | 'gap'> = [];

  pages.forEach((page, index) => {
    if (index > 0 && page - pages[index - 1] > 1) out.push('gap');
    out.push(page);
  });

  return out;
}

/**
 * A page size that survives a reload
 *
 * Somebody who wants 100 rows wants them every time, and re-picking it on
 * every visit is the kind of small friction that makes a list feel unfinished.
 * Storage is per-browser and best-effort: a private window or blocked site
 * data throws on access, so every read and write is guarded and the default
 * stands if anything goes wrong.
 */
export function usePageSize(
  storageKey: string,
  fallback = 20
): [number, (size: number) => void] {
  const [size, setSize] = React.useState<number>(() => {
    try {
      const stored = Number(window.localStorage.getItem(storageKey));
      return PAGE_SIZES.includes(stored as (typeof PAGE_SIZES)[number])
        ? stored
        : fallback;
    } catch {
      return fallback;
    }
  });

  const update = React.useCallback(
    (next: number) => {
      setSize(next);
      try {
        window.localStorage.setItem(storageKey, String(next));
      } catch {
        // Not being able to remember the choice is not a reason to ignore it.
      }
    },
    [storageKey]
  );

  return [size, update];
}
