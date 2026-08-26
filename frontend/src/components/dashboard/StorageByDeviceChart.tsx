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

export const StorageByDeviceChart: React.FC = () => {
  const { data, isLoading, error } = useQuery({
    queryKey: ['storage-by-device'],
    queryFn: async () => {
      // Fetch all devices
      const devicesResponse = await api.get('/devices', {
        params: {
          limit: 100,
          skip: 0,
        },
      });

      const devices = devicesResponse.data.items;

      // Fetch all backups
      const backupsResponse = await api.get('/backups', {
        params: {
          limit: 100,
          skip: 0,
        },
      });

      const backups = backupsResponse.data.items;

      // Calculate storage per device
      const storageMap = new Map<number, { hostname: string; storage: number }>();

      devices.forEach((device: any) => {
        storageMap.set(device.id, { hostname: device.hostname, storage: 0 });
      });

      backups.forEach((backup: any) => {
        if (backup.status === 'success' && backup.file_size) {
          const deviceData = storageMap.get(backup.device_id);
          if (deviceData) {
            deviceData.storage += backup.file_size;
          }
        }
      });

      // Convert to array and sort by storage (descending)
      const storageData: StorageData[] = Array.from(storageMap.values())
        .filter((item) => item.storage > 0)
        .sort((a, b) => b.storage - a.storage)
        .slice(0, 10) // Top 10 devices
        .map((item) => ({
          hostname: item.hostname.length > 15 ? item.hostname.substring(0, 15) + '...' : item.hostname,
          storage: Math.round(item.storage / 1024), // Convert to KB for chart
          storageFormatted: formatFileSize(item.storage),
        }));

      return storageData;
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
            formatter={(value: any, name: any, props: any) => [
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
