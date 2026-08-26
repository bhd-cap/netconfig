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

export const BackupTrendsChart: React.FC<BackupTrendsChartProps> = ({ days = 30 }) => {
  const { data, isLoading, error } = useQuery({
    queryKey: ['backup-trends', days],
    queryFn: async () => {
      // Calculate date range
      const endDate = new Date();
      const startDate = new Date();
      startDate.setDate(startDate.getDate() - days);

      // Fetch backups in date range
      const response = await api.get('/backups', {
        params: {
          limit: 100,
          skip: 0,
        },
      });

      const backups = response.data.items;

      // Group by date
      const trendsMap = new Map<string, { successful: number; failed: number }>();

      // Initialize all dates with 0 counts
      for (let i = 0; i < days; i++) {
        const date = new Date();
        date.setDate(date.getDate() - i);
        const dateStr = format(date, 'yyyy-MM-dd');
        trendsMap.set(dateStr, { successful: 0, failed: 0 });
      }

      // Count backups by date and status
      backups.forEach((backup: any) => {
        const backupDate = format(new Date(backup.backed_up_at), 'yyyy-MM-dd');
        if (trendsMap.has(backupDate)) {
          const counts = trendsMap.get(backupDate)!;
          if (backup.status === 'success') {
            counts.successful++;
          } else if (backup.status === 'failed') {
            counts.failed++;
          }
        }
      });

      // Convert to array and sort by date
      const trendsData: BackupTrendsData[] = Array.from(trendsMap.entries())
        .map(([date, counts]) => ({
          date: format(new Date(date), 'MMM d'),
          successful: counts.successful,
          failed: counts.failed,
        }))
        .reverse(); // Oldest to newest

      return trendsData;
    },
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
