/**
 * Compare Page - Configuration comparison tool
 */
import React, { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Loader2, GitCompare } from 'lucide-react';
import { toast } from 'react-hot-toast';
import api from '../lib/api';
import { Configuration, CompareResponse } from '../types';
import { ConfigSelector } from '../components/compare/ConfigSelector';
import { CompareStats } from '../components/compare/CompareStats';
import { DiffViewer } from '../components/compare/DiffViewer';

export const Compare: React.FC = () => {
  const [compareResult, setCompareResult] = useState<CompareResponse | null>(null);
  const [selectedConfigs, setSelectedConfigs] = useState<{
    config1: Configuration;
    config2: Configuration;
  } | null>(null);

  const compareMutation = useMutation({
    mutationFn: async ({ config1, config2 }: { config1: Configuration; config2: Configuration }) => {
      const response = await api.post<CompareResponse>('/compare/', {
        config1_id: config1.id,
        config2_id: config2.id,
        context_lines: 3,
        include_html: false,
      });
      return response.data;
    },
    onSuccess: (data) => {
      setCompareResult(data);
      toast.success('Comparison complete');
    },
    onError: (error: any) => {
      toast.error(error.response?.data?.detail || 'Comparison failed');
    },
  });

  const handleSelect = (config1: Configuration, config2: Configuration) => {
    setSelectedConfigs({ config1, config2 });
    compareMutation.mutate({ config1, config2 });
  };

  const handleReset = () => {
    setCompareResult(null);
    setSelectedConfigs(null);
  };

  // Fetch config content for diff viewer
  const [configContents, setConfigContents] = useState<{ old: string; new: string } | null>(null);

  React.useEffect(() => {
    const fetchContents = async () => {
      if (compareResult && selectedConfigs) {
        try {
          // For now, we'll use the unified_diff from the API
          // In a real implementation, you might want to fetch the full config files
          setConfigContents({
            old: '', // Would fetch from API if needed
            new: '', // Would fetch from API if needed
          });
        } catch (error) {
          console.error('Failed to fetch config contents:', error);
        }
      }
    };
    fetchContents();
  }, [compareResult, selectedConfigs]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-gray-900">Configuration Comparison</h1>
        <p className="text-gray-600">Compare two device configurations side-by-side</p>
      </div>

      {/* Config Selector */}
      <ConfigSelector
        onSelect={handleSelect}
        onReset={handleReset}
        isComparing={compareMutation.isPending}
      />

      {/* Loading State */}
      {compareMutation.isPending && (
        <div className="bg-white rounded-lg shadow p-12 text-center">
          <Loader2 className="h-12 w-12 animate-spin text-blue-600 mx-auto mb-4" />
          <p className="text-gray-600">Comparing configurations...</p>
        </div>
      )}

      {/* Comparison Results */}
      {compareResult && selectedConfigs && (
        <>
          {/* Statistics */}
          <CompareStats
            statistics={compareResult.statistics}
            config1={compareResult.config1}
            config2={compareResult.config2}
            isIdentical={compareResult.is_identical}
          />

          {/* Diff Viewer */}
          {!compareResult.is_identical && (
            <DiffViewer
              oldValue={compareResult.unified_diff}
              newValue=""
              oldTitle={`${selectedConfigs.config1.device_hostname} - ${new Date(
                selectedConfigs.config1.backed_up_at
              ).toLocaleString()}`}
              newTitle={`${selectedConfigs.config2.device_hostname} - ${new Date(
                selectedConfigs.config2.backed_up_at
              ).toLocaleString()}`}
              unifiedDiff={compareResult.unified_diff}
            />
          )}
        </>
      )}

      {/* Empty State */}
      {!compareResult && !compareMutation.isPending && (
        <div className="bg-white rounded-lg shadow p-12 text-center">
          <GitCompare className="h-16 w-16 text-gray-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">Ready to Compare</h3>
          <p className="text-gray-600">
            Select a device and two configurations above to see the differences
          </p>
        </div>
      )}
    </div>
  );
};
