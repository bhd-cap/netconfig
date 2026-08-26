/**
 * ConfigSelector Component
 * Allows users to select two configurations to compare
 */
import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Server, Calendar, HardDrive } from 'lucide-react';
import api from '../../lib/api';
import { Device, Configuration, PaginatedResponse } from '../../types';
import { format } from 'date-fns';

interface ConfigSelectorProps {
  onSelect: (config1: Configuration, config2: Configuration) => void;
  onReset: () => void;
  isComparing: boolean;
}

export const ConfigSelector: React.FC<ConfigSelectorProps> = ({ onSelect, onReset, isComparing }) => {
  const [selectedDeviceId, setSelectedDeviceId] = useState<number | null>(null);
  const [config1, setConfig1] = useState<Configuration | null>(null);
  const [config2, setConfig2] = useState<Configuration | null>(null);

  // Fetch devices
  const { data: devicesData } = useQuery({
    queryKey: ['devices-for-compare'],
    queryFn: async () => {
      const response = await api.get<PaginatedResponse<Device>>('/devices?limit=100');
      return response.data;
    },
  });

  // Fetch configurations for selected device
  const { data: configurationsData, isLoading: isLoadingConfigs } = useQuery({
    queryKey: ['configurations-for-device', selectedDeviceId],
    queryFn: async () => {
      if (!selectedDeviceId) return null;
      const response = await api.get<PaginatedResponse<Configuration>>(
        `/backups?device_id=${selectedDeviceId}&limit=50`
      );
      return response.data;
    },
    enabled: !!selectedDeviceId,
  });

  const handleDeviceChange = (deviceId: number) => {
    setSelectedDeviceId(deviceId);
    setConfig1(null);
    setConfig2(null);
  };

  const handleCompare = () => {
    if (config1 && config2) {
      onSelect(config1, config2);
    }
  };

  const handleReset = () => {
    setSelectedDeviceId(null);
    setConfig1(null);
    setConfig2(null);
    onReset();
  };

  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
  };

  const canCompare = config1 && config2 && config1.id !== config2.id;

  return (
    <div className="bg-white rounded-lg shadow p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-900">Select Configurations to Compare</h2>
        {(config1 || config2) && (
          <button
            onClick={handleReset}
            className="text-sm text-gray-600 hover:text-gray-900"
          >
            Reset
          </button>
        )}
      </div>

      {/* Device Selector */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">
          Select Device
        </label>
        <select
          value={selectedDeviceId || ''}
          onChange={(e) => handleDeviceChange(Number(e.target.value))}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          disabled={isComparing}
        >
          <option value="">Choose a device...</option>
          {devicesData?.items.map((device) => (
            <option key={device.id} value={device.id}>
              {device.hostname} ({device.ip_address})
            </option>
          ))}
        </select>
      </div>

      {/* Configuration Selectors */}
      {selectedDeviceId && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Config 1 */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Configuration A
            </label>
            {isLoadingConfigs ? (
              <div className="text-sm text-gray-500">Loading configurations...</div>
            ) : configurationsData && configurationsData.items.length > 0 ? (
              <select
                value={config1?.id || ''}
                onChange={(e) => {
                  const selected = configurationsData.items.find(
                    (c) => c.id === Number(e.target.value)
                  );
                  setConfig1(selected || null);
                }}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                disabled={isComparing}
              >
                <option value="">Select configuration...</option>
                {configurationsData.items
                  .filter((c) => c.status === 'success')
                  .map((config) => (
                    <option key={config.id} value={config.id}>
                      {format(new Date(config.backed_up_at), 'MMM d, yyyy HH:mm')} -{' '}
                      {formatFileSize(config.file_size)}
                    </option>
                  ))}
              </select>
            ) : (
              <div className="text-sm text-gray-500">No configurations available</div>
            )}
            {config1 && (
              <div className="mt-2 p-3 bg-blue-50 rounded-lg border border-blue-200">
                <div className="flex items-center text-sm text-blue-900">
                  <Calendar className="h-4 w-4 mr-2" />
                  {format(new Date(config1.backed_up_at), 'MMM d, yyyy HH:mm:ss')}
                </div>
                <div className="flex items-center text-sm text-blue-700 mt-1">
                  <HardDrive className="h-4 w-4 mr-2" />
                  {formatFileSize(config1.file_size)}
                </div>
              </div>
            )}
          </div>

          {/* Config 2 */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Configuration B
            </label>
            {isLoadingConfigs ? (
              <div className="text-sm text-gray-500">Loading configurations...</div>
            ) : configurationsData && configurationsData.items.length > 0 ? (
              <select
                value={config2?.id || ''}
                onChange={(e) => {
                  const selected = configurationsData.items.find(
                    (c) => c.id === Number(e.target.value)
                  );
                  setConfig2(selected || null);
                }}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                disabled={isComparing}
              >
                <option value="">Select configuration...</option>
                {configurationsData.items
                  .filter((c) => c.status === 'success')
                  .map((config) => (
                    <option key={config.id} value={config.id}>
                      {format(new Date(config.backed_up_at), 'MMM d, yyyy HH:mm')} -{' '}
                      {formatFileSize(config.file_size)}
                    </option>
                  ))}
              </select>
            ) : (
              <div className="text-sm text-gray-500">No configurations available</div>
            )}
            {config2 && (
              <div className="mt-2 p-3 bg-green-50 rounded-lg border border-green-200">
                <div className="flex items-center text-sm text-green-900">
                  <Calendar className="h-4 w-4 mr-2" />
                  {format(new Date(config2.backed_up_at), 'MMM d, yyyy HH:mm:ss')}
                </div>
                <div className="flex items-center text-sm text-green-700 mt-1">
                  <HardDrive className="h-4 w-4 mr-2" />
                  {formatFileSize(config2.file_size)}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Compare Button */}
      {selectedDeviceId && (
        <div className="flex justify-end pt-4 border-t">
          <button
            onClick={handleCompare}
            disabled={!canCompare || isComparing}
            className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isComparing ? 'Comparing...' : 'Compare Configurations'}
          </button>
        </div>
      )}

      {/* Validation Messages */}
      {config1 && config2 && config1.id === config2.id && (
        <div className="text-sm text-red-600 text-center">
          Please select two different configurations
        </div>
      )}
    </div>
  );
};
