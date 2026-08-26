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
  Inactive: '#6b7280', // gray
};

export const DeviceHealthChart: React.FC = () => {
  const { data, isLoading, error } = useQuery({
    queryKey: ['device-health'],
    queryFn: async () => {
      const response = await api.get('/devices', {
        params: {
          limit: 100,
          skip: 0,
        },
      });

      const devices = response.data.items;

      // Count devices by status
      const healthCounts = {
        Healthy: 0,
        Warning: 0,
        Critical: 0,
        Inactive: 0,
      };

      devices.forEach((device: any) => {
        if (!device.is_active) {
          healthCounts.Inactive++;
        } else if (device.status === 'healthy') {
          healthCounts.Healthy++;
        } else if (device.status === 'failed') {
          healthCounts.Critical++;
        } else {
          healthCounts.Warning++;
        }
      });

      // Convert to chart data
      const chartData: DeviceHealthData[] = Object.entries(healthCounts)
        .filter(([_, value]) => value > 0)
        .map(([name, value]) => ({
          name,
          value,
          color: HEALTH_COLORS[name as keyof typeof HEALTH_COLORS],
        }));

      return chartData;
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
