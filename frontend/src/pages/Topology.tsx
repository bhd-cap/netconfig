/**
 * Topology Page
 *
 * Draws the graph the backend derives from LLDP and CDP adjacencies, and lets
 * it be rearranged and saved.
 *
 * The graph itself is never stored. A saved diagram holds only the edits -
 * positions, renamed labels, hidden nodes, hand-drawn links - so a device
 * discovered after the diagram was saved still appears, and a layout someone
 * arranged by hand survives the next crawl.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'react-hot-toast';
import {
  Eye,
  EyeOff,
  Link2,
  Loader2,
  Network,
  Plus,
  RefreshCw,
  Save,
  Trash2,
  X,
} from 'lucide-react';
import api from '../lib/api';
import { usePermissions } from '../hooks/usePermissions';
import {
  DiagramLayout,
  TopologyDiagram,
  TopologyGraph,
  TopologyLink,
  TopologyNode,
} from '../types';

const CANVAS_WIDTH = 1200;
const CANVAS_HEIGHT = 720;
const NODE_RADIUS = 26;

interface Positioned extends TopologyNode {
  x: number;
  y: number;
}

/**
 * Give every node a position
 *
 * A node the saved layout already places keeps that position. Everything else
 * is laid out on a circle, which keeps a fresh graph readable without pulling
 * in a physics library: nodes never overlap and the edges are all visible.
 */
function placeNodes(nodes: TopologyNode[]): Positioned[] {
  const unplaced = nodes.filter((node) => node.x == null || node.y == null);
  const radius = Math.min(CANVAS_WIDTH, CANVAS_HEIGHT) / 2 - NODE_RADIUS * 3;
  const centreX = CANVAS_WIDTH / 2;
  const centreY = CANVAS_HEIGHT / 2;

  let index = 0;

  return nodes.map((node) => {
    if (node.x != null && node.y != null) {
      return { ...node, x: node.x, y: node.y } as Positioned;
    }

    const angle = (2 * Math.PI * index) / Math.max(unplaced.length, 1);
    index += 1;

    return {
      ...node,
      x: centreX + radius * Math.cos(angle),
      y: centreY + radius * Math.sin(angle),
    } as Positioned;
  });
}

function nodeColour(node: TopologyNode): string {
  if (!node.managed) return '#94a3b8';
  if (!node.is_active) return '#cbd5e1';
  if (node.last_backup_status === 'failed') return '#ef4444';
  if (node.last_backup_status === 'success') return '#10b981';
  return '#3b82f6';
}

export const Topology: React.FC = () => {
  const queryClient = useQueryClient();
  const { can } = usePermissions();
  const canEdit = can('discovery:write');

  const svgRef = useRef<SVGSVGElement>(null);

  const [diagramId, setDiagramId] = useState<number | null>(null);
  const [includeUnmanaged, setIncludeUnmanaged] = useState(true);
  const [activeOnly, setActiveOnly] = useState(true);
  const [showHidden, setShowHidden] = useState(false);

  const [nodes, setNodes] = useState<Positioned[]>([]);
  const [links, setLinks] = useState<TopologyLink[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [linkFrom, setLinkFrom] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [dragging, setDragging] = useState<string | null>(null);

  const { data: diagrams } = useQuery<TopologyDiagram[]>({
    queryKey: ['diagrams'],
    queryFn: async () => (await api.get('/discovery/diagrams')).data,
  });

  const {
    data: graph,
    isLoading,
    refetch,
    isFetching,
  } = useQuery<TopologyGraph>({
    queryKey: ['topology', diagramId, activeOnly, includeUnmanaged],
    queryFn: async () => {
      const params: Record<string, any> = {
        active_only: activeOnly,
        include_unmanaged: includeUnmanaged,
      };
      if (diagramId) params.diagram_id = diagramId;
      return (await api.get('/discovery/topology', { params })).data;
    },
  });

  // Pick up the default diagram the first time the list arrives, so the
  // layout someone arranged is what they see on landing here.
  useEffect(() => {
    if (diagramId === null && diagrams?.length) {
      const preferred = diagrams.find((diagram) => diagram.is_default);
      if (preferred) setDiagramId(preferred.id);
    }
  }, [diagrams, diagramId]);

  // Reset the working copy whenever a fresh graph arrives. Unsaved drags are
  // deliberately discarded here - the refresh was asked for.
  useEffect(() => {
    if (!graph) return;
    setNodes(placeNodes(graph.nodes));
    setLinks(graph.links);
    setDirty(false);
    setLinkFrom(null);
  }, [graph]);

  const nodesByKey = useMemo(() => {
    const map = new Map<string, Positioned>();
    nodes.forEach((node) => map.set(node.key, node));
    return map;
  }, [nodes]);

  const visibleNodes = useMemo(
    () => nodes.filter((node) => showHidden || !node.hidden),
    [nodes, showHidden]
  );

  const visibleLinks = useMemo(
    () =>
      links.filter((link) => {
        if (!showHidden && link.hidden) return false;
        const source = nodesByKey.get(link.source);
        const target = nodesByKey.get(link.target);
        if (!source || !target) return false;
        if (!showHidden && (source.hidden || target.hidden)) return false;
        return true;
      }),
    [links, nodesByKey, showHidden]
  );

  // ---------------------------------------------------------------- dragging

  const toCanvas = useCallback((event: React.MouseEvent) => {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };

    const rect = svg.getBoundingClientRect();
    // The SVG scales to its container, so screen pixels have to be converted
    // back into viewBox units or the node jumps away from the cursor.
    return {
      x: ((event.clientX - rect.left) / rect.width) * CANVAS_WIDTH,
      y: ((event.clientY - rect.top) / rect.height) * CANVAS_HEIGHT,
    };
  }, []);

  const handleMouseMove = useCallback(
    (event: React.MouseEvent) => {
      if (!dragging) return;
      const { x, y } = toCanvas(event);

      setNodes((current) =>
        current.map((node) => (node.key === dragging ? { ...node, x, y } : node))
      );
      setDirty(true);
    },
    [dragging, toCanvas]
  );

  const handleNodeClick = (key: string) => {
    if (linkFrom && linkFrom !== key) {
      addManualLink(linkFrom, key);
      setLinkFrom(null);
      return;
    }
    setSelected(key === selected ? null : key);
  };

  // ------------------------------------------------------------------- edits

  const addManualLink = (source: string, target: string) => {
    const key = `manual::${[source, target].sort().join('::')}`;

    if (links.some((link) => link.key === key)) {
      toast('Those two are already linked');
      return;
    }

    setLinks((current) => [
      ...current,
      {
        key,
        source,
        target,
        source_interface: null,
        target_interface: null,
        protocol: 'manual',
        is_active: true,
        last_seen: null,
        confirmed_both_ends: false,
        manual: true,
      },
    ]);
    setDirty(true);
    toast.success('Link added; save the diagram to keep it');
  };

  const toggleNodeHidden = (key: string) => {
    setNodes((current) =>
      current.map((node) =>
        node.key === key ? { ...node, hidden: !node.hidden } : node
      )
    );
    setDirty(true);
  };

  const renameNode = (key: string, label: string) => {
    setNodes((current) =>
      current.map((node) => (node.key === key ? { ...node, label } : node))
    );
    setDirty(true);
  };

  const removeLink = (key: string) => {
    const link = links.find((entry) => entry.key === key);
    if (!link) return;

    if (link.manual) {
      setLinks((current) => current.filter((entry) => entry.key !== key));
    } else {
      // A discovered link is a fact, not an opinion: hide it on this diagram
      // rather than pretending the cable is not there.
      setLinks((current) =>
        current.map((entry) =>
          entry.key === key ? { ...entry, hidden: true } : entry
        )
      );
    }
    setDirty(true);
  };

  /** Reduce the working copy to just the edits worth storing */
  const currentLayout = (): DiagramLayout => {
    const nodeEdits: DiagramLayout['nodes'] = {};

    nodes.forEach((node) => {
      const original = graph?.nodes.find((entry) => entry.key === node.key);
      const edit: Record<string, any> = { x: Math.round(node.x), y: Math.round(node.y) };

      if (node.hidden) edit.hidden = true;
      if (original && node.label !== original.label) edit.label = node.label;

      nodeEdits[node.key] = edit;
    });

    return {
      nodes: nodeEdits,
      links: links
        .filter((link) => link.manual)
        .map((link) => ({
          source: link.source,
          target: link.target,
          source_interface: link.source_interface ?? null,
          target_interface: link.target_interface ?? null,
          label: link.label ?? null,
        })),
      hidden_links: links.filter((link) => link.hidden).map((link) => link.key),
    };
  };

  // --------------------------------------------------------------- mutations

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!diagramId) throw new Error('No diagram selected');
      return (
        await api.put(`/discovery/diagrams/${diagramId}`, {
          layout: currentLayout(),
        })
      ).data;
    },
    onSuccess: () => {
      setDirty(false);
      queryClient.invalidateQueries({ queryKey: ['diagrams'] });
      toast.success('Diagram saved');
    },
  });

  const createMutation = useMutation({
    mutationFn: async (name: string) =>
      (
        await api.post('/discovery/diagrams', {
          name,
          layout: currentLayout(),
          is_default: !diagrams?.length,
        })
      ).data as TopologyDiagram,
    onSuccess: (diagram) => {
      queryClient.invalidateQueries({ queryKey: ['diagrams'] });
      setDiagramId(diagram.id);
      setDirty(false);
      toast.success(`Saved as '${diagram.name}'`);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: number) => api.delete(`/discovery/diagrams/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['diagrams'] });
      setDiagramId(null);
      toast.success('Diagram deleted');
    },
  });

  const handleSaveAs = () => {
    const name = window.prompt('Name for this diagram');
    if (name?.trim()) createMutation.mutate(name.trim());
  };

  const selectedNode = selected ? nodesByKey.get(selected) : undefined;
  const selectedLinks = selected
    ? links.filter((link) => link.source === selected || link.target === selected)
    : [];

  // ------------------------------------------------------------------ render

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Network Topology</h1>
          <p className="text-gray-600">
            Built from LLDP and CDP adjacencies. Drag to rearrange, then save.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <select
            value={diagramId ?? ''}
            onChange={(event) =>
              setDiagramId(event.target.value ? Number(event.target.value) : null)
            }
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm"
          >
            <option value="">Discovered layout (unsaved)</option>
            {diagrams?.map((diagram) => (
              <option key={diagram.id} value={diagram.id}>
                {diagram.name}
                {diagram.is_default ? ' (default)' : ''}
              </option>
            ))}
          </select>

          <button
            onClick={() => refetch()}
            className="inline-flex items-center px-3 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50"
          >
            <RefreshCw
              className={`h-4 w-4 mr-2 ${isFetching ? 'animate-spin' : ''}`}
            />
            Refresh
          </button>

          {canEdit && (
            <>
              <button
                onClick={() => saveMutation.mutate()}
                disabled={!diagramId || !dirty || saveMutation.isPending}
                className="inline-flex items-center px-3 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 disabled:opacity-50"
              >
                <Save className="h-4 w-4 mr-2" />
                Save
              </button>

              <button
                onClick={handleSaveAs}
                className="inline-flex items-center px-3 py-2 border border-gray-300 rounded-lg text-sm hover:bg-gray-50"
              >
                <Plus className="h-4 w-4 mr-2" />
                Save as
              </button>

              {diagramId && (
                <button
                  onClick={() => {
                    if (window.confirm('Delete this diagram?')) {
                      deleteMutation.mutate(diagramId);
                    }
                  }}
                  className="inline-flex items-center px-3 py-2 border border-red-300 text-red-700 rounded-lg text-sm hover:bg-red-50"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              )}
            </>
          )}
        </div>
      </div>

      {/* Filters and counts */}
      <div className="bg-white rounded-lg shadow p-4 flex flex-wrap items-center gap-6 text-sm">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={activeOnly}
            onChange={(event) => setActiveOnly(event.target.checked)}
          />
          Only links still being seen
        </label>

        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={includeUnmanaged}
            onChange={(event) => setIncludeUnmanaged(event.target.checked)}
          />
          Include unmanaged neighbours
        </label>

        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={showHidden}
            onChange={(event) => setShowHidden(event.target.checked)}
          />
          Show hidden
        </label>

        <div className="ml-auto flex items-center gap-4 text-gray-600">
          <span>{graph?.stats.managed_nodes ?? 0} managed</span>
          <span>{graph?.stats.unmanaged_nodes ?? 0} unmanaged</span>
          <span>{visibleLinks.length} links</span>
          {dirty && (
            <span className="px-2 py-1 bg-amber-100 text-amber-800 rounded text-xs font-medium">
              Unsaved changes
            </span>
          )}
        </div>
      </div>

      {linkFrom && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 flex items-center justify-between text-sm">
          <span className="text-blue-800">
            Pick the other end of the link from{' '}
            <strong>{nodesByKey.get(linkFrom)?.label}</strong>
          </span>
          <button
            onClick={() => setLinkFrom(null)}
            className="text-blue-700 hover:text-blue-900"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        {/* Canvas */}
        <div className="lg:col-span-3 bg-white rounded-lg shadow overflow-hidden">
          {visibleNodes.length === 0 ? (
            <div className="p-12 text-center text-gray-500">
              <Network className="h-12 w-12 mx-auto mb-4 text-gray-300" />
              <p className="font-medium text-gray-900">Nothing discovered yet</p>
              <p className="text-sm mt-1">
                Run a discovery crawl from a seed device to build the topology.
              </p>
            </div>
          ) : (
            <svg
              ref={svgRef}
              viewBox={`0 0 ${CANVAS_WIDTH} ${CANVAS_HEIGHT}`}
              className="w-full h-[520px] select-none"
              onMouseMove={handleMouseMove}
              onMouseUp={() => setDragging(null)}
              onMouseLeave={() => setDragging(null)}
            >
              <rect width={CANVAS_WIDTH} height={CANVAS_HEIGHT} fill="#f8fafc" />

              {visibleLinks.map((link) => {
                const source = nodesByKey.get(link.source)!;
                const target = nodesByKey.get(link.target)!;
                const isSelected =
                  selected === link.source || selected === link.target;

                return (
                  <g key={link.key}>
                    <line
                      x1={source.x}
                      y1={source.y}
                      x2={target.x}
                      y2={target.y}
                      stroke={
                        link.manual
                          ? '#a855f7'
                          : isSelected
                          ? '#2563eb'
                          : link.confirmed_both_ends
                          ? '#64748b'
                          : '#cbd5e1'
                      }
                      strokeWidth={isSelected ? 3 : 2}
                      strokeDasharray={link.manual ? '6 4' : undefined}
                      opacity={link.hidden ? 0.3 : 1}
                    />
                    {isSelected && link.source_interface && (
                      <text
                        x={(source.x + target.x) / 2}
                        y={(source.y + target.y) / 2 - 6}
                        textAnchor="middle"
                        className="fill-gray-600"
                        fontSize={11}
                      >
                        {link.source_interface}
                        {link.target_interface ? ` ↔ ${link.target_interface}` : ''}
                      </text>
                    )}
                  </g>
                );
              })}

              {visibleNodes.map((node) => (
                <g
                  key={node.key}
                  transform={`translate(${node.x}, ${node.y})`}
                  onMouseDown={() => canEdit && setDragging(node.key)}
                  onClick={() => handleNodeClick(node.key)}
                  className={canEdit ? 'cursor-move' : 'cursor-pointer'}
                  opacity={node.hidden ? 0.35 : 1}
                >
                  <circle
                    r={NODE_RADIUS}
                    fill={nodeColour(node)}
                    stroke={selected === node.key ? '#1d4ed8' : '#ffffff'}
                    strokeWidth={selected === node.key ? 4 : 2}
                  />
                  {!node.managed && (
                    <circle
                      r={NODE_RADIUS}
                      fill="none"
                      stroke="#64748b"
                      strokeWidth={2}
                      strokeDasharray="4 3"
                    />
                  )}
                  <text
                    y={NODE_RADIUS + 16}
                    textAnchor="middle"
                    fontSize={12}
                    className="fill-gray-900 font-medium"
                  >
                    {node.label}
                  </text>
                  <text
                    y={4}
                    textAnchor="middle"
                    fontSize={11}
                    className="fill-white font-semibold"
                  >
                    {node.link_count}
                  </text>
                </g>
              ))}
            </svg>
          )}

          <div className="border-t border-gray-200 px-4 py-2 flex flex-wrap gap-4 text-xs text-gray-600">
            <span className="flex items-center gap-1">
              <span className="inline-block h-3 w-3 rounded-full bg-emerald-500" />
              Backed up
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block h-3 w-3 rounded-full bg-red-500" />
              Last backup failed
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block h-3 w-3 rounded-full bg-blue-500" />
              Managed, not yet backed up
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block h-3 w-3 rounded-full bg-slate-400" />
              Unmanaged neighbour
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block h-3 w-6 border-t-2 border-dashed border-purple-500" />
              Hand-drawn link
            </span>
          </div>
        </div>

        {/* Detail panel */}
        <div className="bg-white rounded-lg shadow p-4">
          {!selectedNode ? (
            <div className="text-sm text-gray-500">
              <p className="font-medium text-gray-900 mb-2">No node selected</p>
              <p>
                Click a node to see its links.
                {canEdit && ' Drag it to move it, then save the diagram.'}
              </p>
            </div>
          ) : (
            <div className="space-y-4">
              <div>
                <div className="flex items-start justify-between">
                  <h3 className="font-semibold text-gray-900 break-all">
                    {selectedNode.label}
                  </h3>
                  <button
                    onClick={() => setSelected(null)}
                    className="text-gray-400 hover:text-gray-600"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
                <p className="text-xs text-gray-500 mt-1">
                  {selectedNode.managed ? 'Managed device' : 'Unmanaged neighbour'}
                </p>
              </div>

              <dl className="text-sm space-y-1">
                {selectedNode.ip_address && (
                  <div className="flex justify-between">
                    <dt className="text-gray-500">Address</dt>
                    <dd className="text-gray-900">{selectedNode.ip_address}</dd>
                  </div>
                )}
                {selectedNode.device_type && (
                  <div className="flex justify-between">
                    <dt className="text-gray-500">Type</dt>
                    <dd className="text-gray-900">{selectedNode.device_type}</dd>
                  </div>
                )}
                {selectedNode.platform && (
                  <div className="text-gray-600 text-xs pt-1 break-words">
                    {selectedNode.platform}
                  </div>
                )}
                <div className="flex justify-between">
                  <dt className="text-gray-500">Links</dt>
                  <dd className="text-gray-900">{selectedLinks.length}</dd>
                </div>
              </dl>

              {canEdit && (
                <div className="flex flex-wrap gap-2 pt-2 border-t">
                  <button
                    onClick={() => {
                      const label = window.prompt(
                        'Label for this node',
                        selectedNode.label
                      );
                      if (label?.trim()) renameNode(selectedNode.key, label.trim());
                    }}
                    className="px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50"
                  >
                    Rename
                  </button>
                  <button
                    onClick={() => toggleNodeHidden(selectedNode.key)}
                    className="inline-flex items-center px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50"
                  >
                    {selectedNode.hidden ? (
                      <Eye className="h-3 w-3 mr-1" />
                    ) : (
                      <EyeOff className="h-3 w-3 mr-1" />
                    )}
                    {selectedNode.hidden ? 'Show' : 'Hide'}
                  </button>
                  <button
                    onClick={() => setLinkFrom(selectedNode.key)}
                    className="inline-flex items-center px-2 py-1 text-xs border border-gray-300 rounded hover:bg-gray-50"
                  >
                    <Link2 className="h-3 w-3 mr-1" />
                    Draw link
                  </button>
                </div>
              )}

              <div className="pt-2 border-t">
                <h4 className="text-xs font-semibold text-gray-500 uppercase mb-2">
                  Connections
                </h4>
                <ul className="space-y-2 max-h-64 overflow-y-auto">
                  {selectedLinks.map((link) => {
                    const otherKey =
                      link.source === selectedNode.key ? link.target : link.source;
                    const other = nodesByKey.get(otherKey);
                    const localPort =
                      link.source === selectedNode.key
                        ? link.source_interface
                        : link.target_interface;

                    return (
                      <li
                        key={link.key}
                        className="text-sm border border-gray-100 rounded p-2"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0">
                            <p className="font-medium text-gray-900 truncate">
                              {other?.label ?? otherKey}
                            </p>
                            <p className="text-xs text-gray-500">
                              {localPort || 'unknown port'} ·{' '}
                              {link.manual ? 'hand-drawn' : link.protocol.toUpperCase()}
                              {link.hidden ? ' · hidden' : ''}
                            </p>
                          </div>
                          {canEdit && (
                            <button
                              onClick={() => removeLink(link.key)}
                              className="text-gray-400 hover:text-red-600 shrink-0"
                              title={
                                link.manual
                                  ? 'Remove this hand-drawn link'
                                  : 'Hide this link on this diagram'
                              }
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          )}
                        </div>
                      </li>
                    );
                  })}
                  {selectedLinks.length === 0 && (
                    <li className="text-sm text-gray-500">No links</li>
                  )}
                </ul>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
