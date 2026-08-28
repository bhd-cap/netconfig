/**
 * DiffViewer Component
 * Displays configuration differences using react-diff-viewer-continued
 *
 * It is given the two configurations, not a diff of them, and computes the
 * comparison itself - which is what makes "changes only" a real switch. The
 * server's unified diff is still what the copy and download buttons hand
 * over, because that is the format anyone pasting it into a ticket wants.
 */
import React, { useState } from 'react';
import ReactDiffViewer, { DiffMethod } from 'react-diff-viewer-continued';
import { Download, Copy, Check, AlertTriangle } from 'lucide-react';
import { toast } from 'react-hot-toast';

interface DiffViewerProps {
  oldValue: string;
  newValue: string;
  oldTitle: string;
  newTitle: string;
  unifiedDiff: string;
  /** The configuration was too large to send in full */
  contentOmitted?: boolean;
}

export const DiffViewer: React.FC<DiffViewerProps> = ({
  oldValue,
  newValue,
  oldTitle,
  newTitle,
  unifiedDiff,
  contentOmitted = false,
}) => {
  const [viewMode, setViewMode] = useState<'split' | 'unified'>('split');
  const [showChangesOnly, setShowChangesOnly] = useState(true);
  const [copied, setCopied] = useState(false);

  // With no content there is nothing to show context from, so the viewer
  // falls back to the unified diff the server produced and says so rather
  // than offering a switch that cannot do anything.
  const diffOnly = contentOmitted || (!oldValue && !newValue);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(unifiedDiff);
      setCopied(true);
      toast.success('Diff copied to clipboard');
      setTimeout(() => setCopied(false), 2000);
    } catch (error) {
      toast.error('Failed to copy to clipboard');
    }
  };

  const handleDownload = () => {
    const blob = new Blob([unifiedDiff], { type: 'text/plain' });
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `config-diff-${new Date().getTime()}.txt`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
    toast.success('Diff downloaded');
  };

  return (
    <div className="bg-white rounded-lg shadow">
      {/* Controls */}
      <div className="p-4 border-b flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-4">
          <h3 className="text-lg font-semibold text-gray-900">Configuration Diff</h3>
        </div>

        <div className="flex items-center gap-2">
          {/* View Mode Toggle */}
          <div className="flex rounded-lg border border-gray-300 overflow-hidden">
            <button
              onClick={() => setViewMode('split')}
              disabled={diffOnly}
              className={`px-3 py-1 text-sm disabled:opacity-50 ${
                !diffOnly && viewMode === 'split'
                  ? 'bg-blue-600 text-white'
                  : 'bg-white text-gray-700 hover:bg-gray-50'
              }`}
            >
              Split
            </button>
            <button
              onClick={() => setViewMode('unified')}
              disabled={diffOnly}
              className={`px-3 py-1 text-sm border-l border-gray-300 disabled:opacity-50 ${
                diffOnly || viewMode === 'unified'
                  ? 'bg-blue-600 text-white'
                  : 'bg-white text-gray-700 hover:bg-gray-50'
              }`}
            >
              Unified
            </button>
          </div>

          {/* Show Changes Only */}
          <label
            className={`flex items-center text-sm ${
              diffOnly ? 'text-gray-400 cursor-not-allowed' : 'text-gray-700'
            }`}
            title={
              diffOnly
                ? 'The configuration is too large to show in full; only the changes are available'
                : undefined
            }
          >
            <input
              type="checkbox"
              checked={showChangesOnly || diffOnly}
              disabled={diffOnly}
              onChange={(e) => setShowChangesOnly(e.target.checked)}
              data-testid="changes-only"
              className="rounded border-gray-300 text-blue-600 focus:ring-blue-500 mr-2 disabled:opacity-50"
            />
            Changes only
          </label>

          {/* Copy Button */}
          <button
            onClick={handleCopy}
            className="inline-flex items-center px-3 py-1 text-sm border border-gray-300 rounded-lg hover:bg-gray-50 transition"
            title="Copy diff"
          >
            {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
          </button>

          {/* Download Button */}
          <button
            onClick={handleDownload}
            className="inline-flex items-center px-3 py-1 text-sm border border-gray-300 rounded-lg hover:bg-gray-50 transition"
            title="Download diff"
          >
            <Download className="h-4 w-4" />
          </button>
        </div>
      </div>

      {diffOnly && (
        <div className="px-4 py-3 bg-amber-50 border-b border-amber-200 flex items-start gap-2 text-sm text-amber-900">
          <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>
            This configuration is too large to send in full, so only the
            changes are shown. Download either backup to read it whole.
          </span>
        </div>
      )}

      {/* Diff Display */}
      <div className="diff-viewer-container">
        <ReactDiffViewer
          oldValue={diffOnly ? unifiedDiff : oldValue}
          newValue={diffOnly ? '' : newValue}
          splitView={!diffOnly && viewMode === 'split'}
          showDiffOnly={showChangesOnly || diffOnly}
          useDarkTheme={false}
          leftTitle={oldTitle}
          rightTitle={newTitle}
          compareMethod={DiffMethod.WORDS}
          styles={{
            variables: {
              light: {
                diffViewerBackground: '#fff',
                addedBackground: '#e6ffec',
                addedColor: '#24292e',
                removedBackground: '#ffeef0',
                removedColor: '#24292e',
                wordAddedBackground: '#acf2bd',
                wordRemovedBackground: '#fdb8c0',
                addedGutterBackground: '#cdffd8',
                removedGutterBackground: '#ffdce0',
                gutterBackground: '#f6f8fa',
                gutterBackgroundDark: '#f3f4f6',
                highlightBackground: '#fffbdd',
                highlightGutterBackground: '#fff5b1',
              },
            },
            line: {
              padding: '2px 10px',
              fontSize: '13px',
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
            },
          }}
        />
      </div>
    </div>
  );
};
