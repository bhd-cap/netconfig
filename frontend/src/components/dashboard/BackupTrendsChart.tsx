/**
 * BackupTrendsChart Component
 * Displays backup trends over time (successful vs failed backups)
 */
import React from 'react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';
import { useQuery } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import api from '../../lib/api';
import { format } from 'date-fns';

interface BackupTrendsData {
  date: string;
  successful: number;
  failed: number;
}

interface BackupTrendsChartProps {
  days?: number;
}

interface BackupTrendsResponse {
  trends: Array<{
    date: string;
    total: number;
    successful: number;
    failed: number;
  }>;
}

export const BackupTrendsChart: React.FC<BackupTrendsChartProps> = ({ days = 30 }) => {
  const { data, isLoading, error } = useQuery({
    queryKey: ['backup-trends', days],
    queryFn: async () => {
      // Grouped by day in SQL over the whole requested window. The previous
      // version asked for the most recent 100 backups and bucketed them
      // client-side, so a busy install saw a 30-day chart built from perhaps
      // a single day of data.
      const response = await api.get<BackupTrendsResponse>(
        '/statistics/backup-trends',
        { params: { days } }
      );

      const byDate = new Map(response.data.trends.map((t) => [t.date, t]));

      // Fill gaps so days with no backups still appear on the axis.
      const filled: BackupTrendsData[] = [];
      for (let i = days - 1; i >= 0; i--) {
        const date = new Date();
        date.setDate(date.getDate() - i);
        const key = format(date, 'yyyy-MM-dd');
        const entry = byDate.get(key);

        filled.push({
          date: format(date, 'MMM d'),
          successful: entry?.successful ?? 0,
          failed: entry?.failed ?? 0,
        });
      }

      return filled;
    },
    staleTime: 60000,
    refetchInterval: 60000, // Refetch every minute
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-64 text-red-600">
        Failed to load backup trends
      </div>
    );
  }

  return (
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 12 }}
            interval="preserveStartEnd"
          />
          <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
          <Tooltip
            contentStyle={{
              backgroundColor: 'white',
              border: '1px solid #e5e7eb',
              borderRadius: '0.5rem',
            }}
          />
          <Legend />
          <Line
            type="monotone"
            dataKey="successful"
            stroke="#10b981"
            strokeWidth={2}
            name="Successful"
            dot={{ fill: '#10b981', r: 3 }}
            activeDot={{ r: 5 }}
          />
          <Line
            type="monotone"
            dataKey="failed"
            stroke="#ef4444"
            strokeWidth={2}
            name="Failed"
            dot={{ fill: '#ef4444', r: 3 }}
            activeDot={{ r: 5 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
};
