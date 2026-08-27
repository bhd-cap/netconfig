/**
 * CompareStats Component
 * Displays statistics from configuration comparison
 */
import React from 'react';
import { Plus, Minus, Edit2, FileText, CheckCircle } from 'lucide-react';

interface CompareStatsProps {
  statistics: {
    added_lines: number;
    removed_lines: number;
    changed_sections: number;
    total_changes: number;
  };
  config1: {
    path: string;
    label?: string;
    line_count: number;
  };
  config2: {
    path: string;
    label?: string;
    line_count: number;
  };
  isIdentical: boolean;
}

export const CompareStats: React.FC<CompareStatsProps> = ({
  statistics,
  config1,
  config2,
  isIdentical,
}) => {
  if (isIdentical) {
    return (
      <div className="bg-white rounded-lg shadow p-6">
        <div className="flex items-center justify-center text-green-600">
          <CheckCircle className="h-12 w-12 mr-4" />
          <div>
            <h3 className="text-lg font-semibold">Configurations are Identical</h3>
            <p className="text-sm text-gray-600">No differences detected between the two configurations</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-lg shadow p-6">
      <h3 className="text-lg font-semibold text-gray-900 mb-4">Comparison Statistics</h3>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        {/* Added Lines */}
        <div className="bg-green-50 rounded-lg p-4 border border-green-200">
          <div className="flex items-center justify-between">
            <Plus className="h-8 w-8 text-green-600" />
            <span className="text-2xl font-bold text-green-900">{statistics.added_lines}</span>
          </div>
          <p className="text-sm text-green-700 mt-2">Added Lines</p>
        </div>

        {/* Removed Lines */}
        <div className="bg-red-50 rounded-lg p-4 border border-red-200">
          <div className="flex items-center justify-between">
            <Minus className="h-8 w-8 text-red-600" />
            <span className="text-2xl font-bold text-red-900">{statistics.removed_lines}</span>
          </div>
          <p className="text-sm text-red-700 mt-2">Removed Lines</p>
        </div>

        {/* Changed Sections */}
        <div className="bg-blue-50 rounded-lg p-4 border border-blue-200">
          <div className="flex items-center justify-between">
            <Edit2 className="h-8 w-8 text-blue-600" />
            <span className="text-2xl font-bold text-blue-900">{statistics.changed_sections}</span>
          </div>
          <p className="text-sm text-blue-700 mt-2">Changed Sections</p>
        </div>

        {/* Total Changes */}
        <div className="bg-gray-50 rounded-lg p-4 border border-gray-200">
          <div className="flex items-center justify-between">
            <FileText className="h-8 w-8 text-gray-600" />
            <span className="text-2xl font-bold text-gray-900">{statistics.total_changes}</span>
          </div>
          <p className="text-sm text-gray-700 mt-2">Total Changes</p>
        </div>
      </div>

      {/* Configuration Info */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-4 border-t">
        <div>
          <h4 className="text-sm font-medium text-gray-700 mb-2">Configuration A</h4>
          <p className="text-xs text-gray-600 mb-1">{config1.label || config1.path}</p>
          <p className="text-sm text-gray-900">{config1.line_count.toLocaleString()} lines</p>
        </div>
        <div>
          <h4 className="text-sm font-medium text-gray-700 mb-2">Configuration B</h4>
          <p className="text-xs text-gray-600 mb-1">{config2.label || config2.path}</p>
          <p className="text-sm text-gray-900">{config2.line_count.toLocaleString()} lines</p>
        </div>
      </div>
    </div>
  );
};
