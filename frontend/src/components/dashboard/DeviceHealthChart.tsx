/**
 * DeviceHealthChart Component
 * Displays device health distribution as a pie chart
 */
import React from 'react';
import {
  PieChart,
  Pie,
  Cell,
  ResponsiveContainer,
  Tooltip,
  Legend,
} from 'recharts';
import { useQuery } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import api from '../../lib/api';

interface DeviceHealthData {
  name: string;
  value: number;
  color: string;
}

const HEALTH_COLORS = {
  Healthy: '#10b981', // green
  Warning: '#f59e0b', // amber
  Critical: '#ef4444', // red
  Unknown: '#6b7280', // gray
};

interface DeviceHealthResponse {
  summary: {
    total_devices: number;
    healthy: number;
    warning: number;
    critical: number;
    unknown: number;
  };
}

export const DeviceHealthChart: React.FC = () => {
  const { data, isLoading, error } = useQuery({
    queryKey: ['device-health'],
    queryFn: async () => {
      // The backend aggregates health buckets in SQL. This used to pull a
      // page of 100 full device records every minute and count them here,
      // which both transferred far more data than the four numbers it needed
      // and silently ignored every device past the first 100.
      const response = await api.get<DeviceHealthResponse>(
        '/statistics/device-health',
        { params: { limit: 1 } }
      );

      const { healthy, warning, critical, unknown } = response.data.summary;

      const counts: Array<[keyof typeof HEALTH_COLORS, number]> = [
        ['Healthy', healthy],
        ['Warning', warning],
        ['Critical', critical],
        ['Unknown', unknown],
      ];

      return counts
        .filter(([, value]) => value > 0)
        .map(([name, value]) => ({
          name,
          value,
          color: HEALTH_COLORS[name],
        })) as DeviceHealthData[];
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
        Failed to load device health data
      </div>
    );
  }

  if (!data || data.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-500">
        No devices available
      </div>
    );
  }

  return (
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="50%"
            labelLine={false}
            label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
            outerRadius={80}
            fill="#8884d8"
            dataKey="value"
          >
            {data.map((entry, index) => (
              <Cell key={`cell-${index}`} fill={entry.color} />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{
              backgroundColor: 'white',
              border: '1px solid #e5e7eb',
              borderRadius: '0.5rem',
            }}
          />
          <Legend />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
};
