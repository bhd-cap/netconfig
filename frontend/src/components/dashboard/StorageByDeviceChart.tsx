/**
 * StorageByDeviceChart Component
 * Displays storage usage by device as a bar chart
 */
import React from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { useQuery } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import api from '../../lib/api';

interface StorageData {
  hostname: string;
  storage: number;
  storageFormatted: string;
}

const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const sizes = ['Bytes', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
};

interface StorageByDeviceResponse {
  devices: Array<{
    device_id: number;
    hostname: string;
    backup_count: number;
    total_bytes: number;
    total_mb: number;
    avg_bytes: number;
    avg_mb: number;
  }>;
}

export const StorageByDeviceChart: React.FC = () => {
  const { data, isLoading, error } = useQuery({
    queryKey: ['storage-by-device'],
    queryFn: async () => {
      // One grouped query server-side. Previously this fetched 100 devices
      // and 100 backups and joined them in the browser, so per-device totals
      // were wrong as soon as the organization had more than 100 backups.
      const response = await api.get<StorageByDeviceResponse>(
        '/statistics/storage-by-device',
        { params: { limit: 10 } }
      );

      return response.data.devices.map((device) => ({
        hostname:
          device.hostname.length > 15
            ? device.hostname.substring(0, 15) + '...'
            : device.hostname,
        storage: Math.round(device.total_bytes / 1024), // KB for the chart
        storageFormatted: formatFileSize(device.total_bytes),
      })) as StorageData[];
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
        Failed to load storage data
      </div>
    );
  }

  if (!data || data.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-gray-500">
        No backup storage data available
      </div>
    );
  }

  return (
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 5, right: 30, left: 100, bottom: 5 }}
        >
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis
            type="number"
            tick={{ fontSize: 12 }}
            label={{ value: 'Storage (KB)', position: 'insideBottom', offset: -5 }}
          />
          <YAxis
            type="category"
            dataKey="hostname"
            tick={{ fontSize: 12 }}
            width={95}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: 'white',
              border: '1px solid #e5e7eb',
              borderRadius: '0.5rem',
            }}
            formatter={(_value: any, _name: any, props: any) => [
              props.payload.storageFormatted,
              'Storage',
            ]}
          />
          <Bar dataKey="storage" fill="#3b82f6" radius={[0, 4, 4, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
};
