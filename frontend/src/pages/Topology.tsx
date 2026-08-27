/**
 * Topology Page
 *
 * Draws the graph the backend derives from LLDP and CDP adjacencies, and lets
 * it be rearranged and saved.
 *
 * The diagram is the page. Everything else - filters, the legend, a selected
 * node's details - floats over it, because a map you have to scroll to see is
 * not a map. A force simulation with a per-tier Y target does the arranging:
 * the core settles at the top and access switches below it, while the links
 * pull related devices together, so the shape of the network is legible
 * without anyone dragging a node.
 *
 * Only infrastructure is drawn by default - the core, what hangs off it, and
 * unmanaged switches and firewalls at the edge. End hosts are a drill-down:
 * a switch with 200 MACs behind it makes a diagram nobody can read, and those
 * hosts are already in the inventory. Click a node and ask for its hosts to
 * unfold just that one.
 *
 * The graph itself is never stored. A saved diagram holds only the edits -
 * positions, renamed labels, hidden nodes, hand-drawn links - so a device
 * discovered after the diagram was saved still appears, and a layout someone
 * arranged by hand survives the next crawl. A node the saved diagram places is
 * pinned, and the simulation leaves it exactly where it was put.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'react-hot-toast';
import {
  Crosshair,
  ExternalLink,
  Eye,
  EyeOff,
  Layers,
  Link2,
  Loader2,
  Maximize2,
  Minimize2,
  Minus,
  Network,
  Plus,
  RefreshCw,
  Save,
  Shuffle,
  SlidersHorizontal,
  Trash2,
  Users,
  X,
} from 'lucide-react';
import api from '../lib/api';
import { usePermissions } from '../hooks/usePermissions';
import {
  DeviceIcon,
  DeviceKindBadge,
  KIND_LABELS,
  type DeviceKind,
} from '../components/topology/DeviceIcon';
import {
  useForceLayout,
  type LayoutLink,
  type LayoutNode,
} from '../components/topology/useForceLayout';
import {
  ALL_TIERS,
  DiagramLayout,
  INFRASTRUCTURE_TIERS,
  Tier,
  TopologyDiagram,
  TopologyGraph,
  TopologyLink,
  TopologyNode,
} from '../types';

const TIER_LABELS: Record<Tier, string> = {
  core: 'Core',
  distribution: 'Distribution',
  access: 'Access',
  edge: 'Unmanaged edge',
  host: 'End hosts',
};

const TIER_HINTS: Record<Tier, string> = {
  core: 'Managed devices linking several others together',
  distribution: 'Managed devices linking at least one other',
  access: 'Managed devices with nothing further behind them',
  edge: 'Switches, firewalls and routers we do not manage',
  host: 'Phones, access points and workstations - usually best left folded',
};

/** Which kinds appear in the legend, in the order they are drawn there. */
const LEGEND_KINDS: DeviceKind[] = [
  'router',
  'switch',
  'firewall',
  'wireless',
  'server',
  'host',
];

const ZOOM_LIMITS = { min: 0.25, max: 3 };

function tierOf(node: TopologyNode): Tier {
  return node.tier ?? (node.managed ? 'access' : 'host');
}

/**
 * The colour a node is drawn in
 *
 * Status, not vendor: the question a map answers at a glance is "what is
 * wrong", and the icon already says what the thing is.
 */
function nodeColour(node: TopologyNode): string {
  if (!node.managed) return '#64748b';
  if (!node.is_active) return '#94a3b8';
  if (node.last_backup_status === 'failed') return '#dc2626';
  if (node.last_auth_status && node.last_auth_status !== 'success') return '#d97706';
  if (node.last_backup_status === 'success') return '#059669';
  return '#2563eb';
}

/** The core is drawn largest, an end host smallest. */
function nodeRadius(node: TopologyNode): number {
  const tier = tierOf(node);
  if (tier === 'core') return 30;
  if (tier === 'distribution') return 26;
  if (tier === 'host') return 16;
  return 23;
}

export const Topology: React.FC = () => {
  const queryClient = useQueryClient();
  const { can } = usePermissions();
  const canEdit = can('discovery:write');

  const svgRef = useRef<SVGSVGElement>(null);
  const frameRef = useRef<HTMLDivElement | null>(null);
  const observerRef = useRef<ResizeObserver | null>(null);

  const [diagramId, setDiagramId] = useState<number | null>(null);
  const [includeUnmanaged, setIncludeUnmanaged] = useState(true);
  const [activeOnly, setActiveOnly] = useState(true);
  const [showHidden, setShowHidden] = useState(false);
  const [showControls, setShowControls] = useState(false);

  // Infrastructure only to begin with. The host tier is opt-in because it is
  // what turns a diagram into a hairball.
  const [tiers, setTiers] = useState<Tier[]>([...INFRASTRUCTURE_TIERS]);

  // Node keys whose end hosts have been unfolded - the drill-down.
  const [expanded, setExpanded] = useState<string[]>([]);

  // Opened with ?view=full - the "open in a new tab" link lands here, and it
  // covers the application chrome rather than merely widening a column.
  const [fullscreen, setFullscreen] = useState(
    () =>
      typeof window !== 'undefined' &&
      new URLSearchParams(window.location.search).get('view') === 'full'
  );

  const [nodes, setNodes] = useState<TopologyNode[]>([]);
  const [links, setLinks] = useState<TopologyLink[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [linkFrom, setLinkFrom] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [dragging, setDragging] = useState<string | null>(null);

  // Where the saved diagram put things. A key in here is pinned: somebody
  // arranged it deliberately and the simulation must not shuffle it back.
  const [pinned, setPinned] = useState<Map<string, { x: number; y: number }>>(
    new Map()
  );

  // Pan and zoom, applied as one transform over the whole scene.
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const panRef = useRef<{ x: number; y: number; view: typeof view } | null>(null);

  // The canvas is sized to its container rather than a fixed viewBox, so the
  // simulation lays out for the space actually available.
  const [size, setSize] = useState({ width: 1200, height: 720 });

  // A callback ref rather than an effect on mount: the frame is inside a
  // branch that does not render while the first query is in flight, so an
  // effect with an empty dependency list would attach to nothing and the
  // canvas would keep its default size for the life of the page.
  const attachFrame = useCallback((frame: HTMLDivElement | null) => {
    frameRef.current = frame;
    observerRef.current?.disconnect();

    if (!frame) return;

    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width > 0 && height > 0) {
        setSize({ width: Math.round(width), height: Math.round(height) });
      }
    });

    observer.observe(frame);
    observerRef.current = observer;
  }, []);

  useEffect(() => () => observerRef.current?.disconnect(), []);

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
    queryKey: [
      'topology',
      diagramId,
      activeOnly,
      includeUnmanaged,
      tiers.join(','),
      expanded.join(','),
    ],
    queryFn: async () => {
      const params: Record<string, any> = {
        active_only: activeOnly,
        include_unmanaged: includeUnmanaged,
        tiers: tiers.join(','),
      };
      if (expanded.length) params.expand = expanded.join(',');
      if (diagramId) params.diagram_id = diagramId;
      return (await api.get('/discovery/topology', { params })).data;
    },
    // An empty tier selection would ask the API for nothing, and its default
    // would quietly put the infrastructure back.
    enabled: tiers.length > 0,
  });

  // Pick up the default diagram the first time the list arrives, so the
  // layout someone arranged is what they see on landing here.
  useEffect(() => {
    if (diagramId === null && diagrams?.length) {
      const preferred = diagrams.find((diagram) => diagram.is_default);
      if (preferred) setDiagramId(preferred.id);
    }
  }, [diagrams, diagramId]);

  // Fold the fresh graph into the working copy. Renames and hidden flags from
  // this session are kept: drilling into a switch refetches, and losing a
  // half-arranged diagram to a drill-down click would be maddening.
  useEffect(() => {
    if (!graph) return;

    setNodes((current) => {
      const previous = new Map(current.map((node) => [node.key, node]));

      return graph.nodes.map((node) => {
        const seen = previous.get(node.key);
        if (!seen) return node;
        return { ...node, hidden: seen.hidden, label: seen.label };
      });
    });

    // Positions the API returned come from the saved diagram, so they are
    // pins rather than suggestions.
    setPinned((current) => {
      const next = new Map(current);
      graph.nodes.forEach((node) => {
        if (node.x != null && node.y != null && !next.has(node.key)) {
          next.set(node.key, { x: node.x, y: node.y });
        }
      });
      return next;
    });

    setLinks((current) => {
      // The API already returns the saved diagram's manual links; these are
      // the ones drawn since the last save.
      const fresh = new Set(graph.links.map((link) => link.key));
      const unsavedManual = current.filter(
        (link) => link.manual && !fresh.has(link.key)
      );
      const hiddenHere = new Set(
        current.filter((link) => link.hidden).map((link) => link.key)
      );

      return [
        ...graph.links.map((link) =>
          hiddenHere.has(link.key) ? { ...link, hidden: true } : link
        ),
        ...unsavedManual,
      ];
    });

    setLinkFrom(null);
  }, [graph]);

  // Switching diagram is a fresh start; whatever was unsaved belonged to the
  // diagram being left.
  useEffect(() => {
    setDirty(false);
    setExpanded([]);
    setPinned(new Map());
  }, [diagramId]);

  // Escape leaves the expanded view, which is the only way out when the page
  // is covering the navigation.
  useEffect(() => {
    if (!fullscreen) return;

    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setFullscreen(false);
    };

    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [fullscreen]);

  const nodesByKey = useMemo(() => {
    const map = new Map<string, TopologyNode>();
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

  // ------------------------------------------------------------------ layout

  const layoutNodes = useMemo<LayoutNode[]>(
    () =>
      visibleNodes.map((node) => {
        const pin = pinned.get(node.key);
        return {
          key: node.key,
          tier: tierOf(node),
          radius: nodeRadius(node),
          pinned: !!pin,
          x: pin?.x,
          y: pin?.y,
        };
      }),
    [visibleNodes, pinned]
  );

  const layoutLinks = useMemo<LayoutLink[]>(
    () => visibleLinks.map((link) => ({ source: link.source, target: link.target })),
    [visibleLinks]
  );

  // Restart the simulation when the shape of the graph changes, not on every
  // render: a re-run for a hover would make the picture jump under the cursor.
  const signature = useMemo(
    () =>
      [
        visibleNodes.map((node) => node.key).join('|'),
        visibleLinks.length,
        [...pinned.keys()].sort().join('|'),
      ].join('#'),
    [visibleNodes, visibleLinks, pinned]
  );

  const { positions, dragTo, endDrag, reheat, running, bands } = useForceLayout(
    layoutNodes,
    layoutLinks,
    { width: size.width, height: size.height, signature }
  );

  const placed = positions();

  // --------------------------------------------------------- tiers and hosts

  const showingHosts = tiers.includes('host');

  const toggleTier = (tier: Tier) => {
    setTiers((current) => {
      if (current.includes(tier)) {
        // Something has to be left on the canvas.
        if (current.length === 1) return current;
        return current.filter((entry) => entry !== tier);
      }
      // Kept in tier order so the chips and the query string read the same
      // way whichever order they were clicked in.
      return ALL_TIERS.filter((entry) => entry === tier || current.includes(entry));
    });
    setSelected(null);
  };

  const toggleExpanded = (key: string) => {
    setExpanded((current) =>
      current.includes(key)
        ? current.filter((entry) => entry !== key)
        : [...current, key]
    );
  };

  /** Open the diagram on its own, with the whole window to work with */
  const openInNewTab = () => {
    const url = new URL(window.location.href);
    url.pathname = '/topology';
    url.searchParams.set('view', 'full');
    window.open(url.toString(), '_blank', 'noopener');
  };

  // ------------------------------------------------------- pan, zoom and drag

  /** Screen coordinates to scene coordinates, undoing the pan and zoom */
  const toScene = useCallback(
    (event: { clientX: number; clientY: number }) => {
      const svg = svgRef.current;
      if (!svg) return { x: 0, y: 0 };

      const rect = svg.getBoundingClientRect();
      return {
        x: (event.clientX - rect.left - view.x) / view.k,
        y: (event.clientY - rect.top - view.y) / view.k,
      };
    },
    [view]
  );

  const zoomBy = useCallback(
    (factor: number, about?: { x: number; y: number }) => {
      setView((current) => {
        const k = Math.min(
          ZOOM_LIMITS.max,
          Math.max(ZOOM_LIMITS.min, current.k * factor)
        );
        if (k === current.k) return current;

        // Zoom about the cursor, or the middle when there is no cursor, so
        // what somebody is looking at stays where it was.
        const focus = about ?? { x: size.width / 2, y: size.height / 2 };
        const ratio = k / current.k;

        return {
          k,
          x: focus.x - (focus.x - current.x) * ratio,
          y: focus.y - (focus.y - current.y) * ratio,
        };
      });
    },
    [size.height, size.width]
  );

  const handleWheel = useCallback(
    (event: React.WheelEvent) => {
      const rect = svgRef.current?.getBoundingClientRect();
      if (!rect) return;

      zoomBy(
        event.deltaY < 0 ? 1.12 : 1 / 1.12,
        { x: event.clientX - rect.left, y: event.clientY - rect.top }
      );
    },
    [zoomBy]
  );

  /**
   * Frame the whole graph in the canvas
   *
   * Without this the diagram sits wherever the simulation left it, at whatever
   * scale it happens to occupy - usually a cluster in the middle of a lot of
   * empty grid. Fitting to the bounding box is what makes the picture use the
   * space, and it is what the reset control means.
   */
  const fitToView = useCallback(() => {
    const spots = [...positions().values()];
    if (spots.length === 0) {
      setView({ x: 0, y: 0, k: 1 });
      return;
    }

    const xs = spots.map((spot) => spot.x);
    const ys = spots.map((spot) => spot.y);
    // Padding leaves room for the labels under the nodes and the overlays in
    // the corners.
    const pad = 70;
    const minX = Math.min(...xs) - pad;
    const maxX = Math.max(...xs) + pad;
    const minY = Math.min(...ys) - pad;
    const maxY = Math.max(...ys) + pad;

    const spanX = Math.max(maxX - minX, 1);
    const spanY = Math.max(maxY - minY, 1);

    const k = Math.min(
      ZOOM_LIMITS.max,
      Math.max(ZOOM_LIMITS.min, Math.min(size.width / spanX, size.height / spanY))
    );

    setView({
      k,
      x: size.width / 2 - ((minX + maxX) / 2) * k,
      y: size.height / 2 - ((minY + maxY) / 2) * k,
    });
  }, [positions, size.height, size.width]);

  const resetView = fitToView;

  // Frame the graph once it has settled, and again whenever the shape of it
  // changes. `running` going false is the simulation saying it is done.
  const framedFor = useRef<string>('');
  useEffect(() => {
    if (running || !visibleNodes.length) return;
    if (framedFor.current === signature) return;

    framedFor.current = signature;
    fitToView();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, signature, visibleNodes.length]);

  const handleMouseMove = useCallback(
    (event: React.MouseEvent) => {
      if (dragging) {
        const { x, y } = toScene(event);
        dragTo(dragging, x, y);
        return;
      }

      const pan = panRef.current;
      if (pan) {
        setView({
          k: pan.view.k,
          x: pan.view.x + (event.clientX - pan.x),
          y: pan.view.y + (event.clientY - pan.y),
        });
      }
    },
    [dragTo, dragging, toScene]
  );

  const stopInteraction = useCallback(() => {
    if (dragging) {
      const spot = positions().get(dragging);
      if (spot) {
        // Dropping a node pins it: that is what dragging one means, and
        // without the pin the simulation pulls it straight back.
        setPinned((current) => new Map(current).set(dragging, spot));
        setDirty(true);
      }
      endDrag(dragging, true);
      setDragging(null);
    }
    panRef.current = null;
  }, [dragging, endDrag, positions]);

  const handleNodeClick = (key: string) => {
    if (linkFrom && linkFrom !== key) {
      addManualLink(linkFrom, key);
      setLinkFrom(null);
      return;
    }
    setSelected(key === selected ? null : key);
  };

  /** Let the simulation lay everything out again, pins and all */
  const relayout = () => {
    setPinned(new Map());
    reheat();
    setDirty(true);
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
    const current = positions();

    nodes.forEach((node) => {
      const spot = current.get(node.key) ?? pinned.get(node.key);
      const original = graph?.nodes.find((entry) => entry.key === node.key);
      const edit: Record<string, any> = {};

      if (spot) {
        edit.x = Math.round(spot.x);
        edit.y = Math.round(spot.y);
      }
      if (node.hidden) edit.hidden = true;
      if (original && node.label !== original.label) edit.label = node.label;

      if (Object.keys(edit).length) nodeEdits[node.key] = edit;
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

  // ------------------------------------------------------------------- focus

  /**
   * What the cursor is pointing at, and what it touches
   *
   * Hovering a node dims everything it is not connected to. On a diagram of
   * any size that is the difference between "there is a line in there
   * somewhere" and seeing a device's neighbourhood immediately.
   */
  const focus = hovered ?? selected;

  const focusedKeys = useMemo(() => {
    if (!focus) return null;

    const keys = new Set<string>([focus]);
    visibleLinks.forEach((link) => {
      if (link.source === focus) keys.add(link.target);
      if (link.target === focus) keys.add(link.source);
    });
    return keys;
  }, [focus, visibleLinks]);

  const dim = (key: string) => (focusedKeys && !focusedKeys.has(key) ? 0.22 : 1);

  // ------------------------------------------------------------------ render

  const shell = fullscreen
    ? 'fixed inset-0 z-40 bg-white flex flex-col'
    : '-m-4 lg:-m-8 flex flex-col h-[calc(100vh-4rem)] bg-white';

  return (
    <div className={shell}>
      {/* Toolbar: deliberately one slim row, so the diagram gets the rest */}
      <div className="shrink-0 flex flex-wrap items-center gap-2 px-4 py-2 border-b border-gray-200 bg-white">
        <Network className="h-5 w-5 text-blue-600 shrink-0" />
        <h1 className="text-sm font-semibold text-gray-900 mr-2">Topology</h1>

        <select
          value={diagramId ?? ''}
          onChange={(event) =>
            setDiagramId(event.target.value ? Number(event.target.value) : null)
          }
          className="px-2 py-1 border border-gray-300 rounded text-sm max-w-[12rem]"
          title="Saved diagram"
        >
          <option value="">Live graph</option>
          {diagrams?.map((diagram) => (
            <option key={diagram.id} value={diagram.id}>
              {diagram.name}
              {diagram.is_default ? ' (default)' : ''}
            </option>
          ))}
        </select>

        <button
          onClick={() => setShowControls((open) => !open)}
          data-testid="topology-controls"
          className={`inline-flex items-center px-2 py-1 border rounded text-sm ${
            showControls
              ? 'border-blue-300 bg-blue-50 text-blue-700'
              : 'border-gray-300 hover:bg-gray-50'
          }`}
        >
          <SlidersHorizontal className="h-4 w-4 mr-1.5" />
          Layers &amp; filters
        </button>

        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="inline-flex items-center px-2 py-1 border border-gray-300 rounded text-sm hover:bg-gray-50 disabled:opacity-50"
        >
          <RefreshCw className={`h-4 w-4 mr-1.5 ${isFetching ? 'animate-spin' : ''}`} />
          Refresh
        </button>

        <button
          onClick={relayout}
          data-testid="topology-relayout"
          className="inline-flex items-center px-2 py-1 border border-gray-300 rounded text-sm hover:bg-gray-50"
          title="Let the simulation arrange everything again, discarding hand placement"
        >
          <Shuffle className="h-4 w-4 mr-1.5" />
          Re-arrange
        </button>

        <div className="ml-auto flex items-center gap-2">
          {dirty && (
            <span className="px-2 py-1 bg-amber-100 text-amber-800 rounded text-xs font-medium">
              Unsaved
            </span>
          )}
          {running && (
            <span className="text-xs text-gray-400 hidden sm:inline">settling…</span>
          )}

          {canEdit && (
            <>
              {diagramId && (
                <button
                  onClick={() => saveMutation.mutate()}
                  disabled={saveMutation.isPending || !dirty}
                  className="inline-flex items-center px-2 py-1 bg-blue-600 text-white rounded text-sm hover:bg-blue-700 disabled:opacity-50"
                >
                  <Save className="h-4 w-4 mr-1.5" />
                  Save
                </button>
              )}
              <button
                onClick={handleSaveAs}
                className="inline-flex items-center px-2 py-1 border border-gray-300 rounded text-sm hover:bg-gray-50"
              >
                <Plus className="h-4 w-4 mr-1.5" />
                Save as
              </button>
              {diagramId && (
                <button
                  onClick={() => {
                    if (window.confirm('Delete this saved diagram?')) {
                      deleteMutation.mutate(diagramId);
                    }
                  }}
                  className="inline-flex items-center px-2 py-1 border border-red-300 text-red-700 rounded text-sm hover:bg-red-50"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              )}
            </>
          )}

          <button
            onClick={openInNewTab}
            className="inline-flex items-center px-2 py-1 border border-gray-300 rounded text-sm hover:bg-gray-50"
            title="Open the diagram in its own tab"
          >
            <ExternalLink className="h-4 w-4" />
          </button>
          <button
            onClick={() => setFullscreen((value) => !value)}
            className="inline-flex items-center px-2 py-1 border border-gray-300 rounded text-sm hover:bg-gray-50"
            title={fullscreen ? 'Leave full screen (Esc)' : 'Full screen'}
          >
            {fullscreen ? (
              <Minimize2 className="h-4 w-4" />
            ) : (
              <Maximize2 className="h-4 w-4" />
            )}
          </button>
        </div>
      </div>

      {/* The diagram, and everything that floats over it */}
      <div ref={attachFrame} className="relative flex-1 min-h-0 bg-slate-50">
        {isLoading ? (
          <div className="absolute inset-0 flex items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
          </div>
        ) : visibleNodes.length === 0 ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-gray-500">
            <Network className="h-12 w-12 mb-4 text-gray-300" />
            <p className="font-medium text-gray-900">Nothing discovered yet</p>
            <p className="text-sm mt-1">
              Run a discovery crawl from a seed device to build the topology.
            </p>
          </div>
        ) : (
          <svg
            ref={svgRef}
            data-testid="topology-canvas"
            width={size.width}
            height={size.height}
            className={`select-none ${
              dragging ? 'cursor-grabbing' : panRef.current ? 'cursor-grabbing' : ''
            }`}
            onMouseDown={(event) => {
              // A drag that starts on the background pans the view.
              if (event.target === event.currentTarget || (event.target as Element).tagName === 'rect') {
                panRef.current = { x: event.clientX, y: event.clientY, view };
              }
            }}
            onMouseMove={handleMouseMove}
            onMouseUp={stopInteraction}
            onMouseLeave={stopInteraction}
            onWheel={handleWheel}
          >
            <defs>
              {/* A soft grid, so panning and zooming have something to read
                  against - an empty background gives no sense of movement. */}
              <pattern
                id="topology-grid"
                width={28}
                height={28}
                patternUnits="userSpaceOnUse"
              >
                <path
                  d="M 28 0 L 0 0 0 28"
                  fill="none"
                  stroke="#e2e8f0"
                  strokeWidth={1}
                />
              </pattern>
              <filter id="node-shadow" x="-50%" y="-50%" width="200%" height="200%">
                <feDropShadow
                  dx="0"
                  dy="1"
                  stdDeviation="1.5"
                  floodColor="#0f172a"
                  floodOpacity="0.18"
                />
              </filter>
            </defs>

            <rect width={size.width} height={size.height} fill="url(#topology-grid)" />

            <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
              {/* The tier a device lands in comes from how many other
                  infrastructure nodes it links to, not from its name. Labelling
                  the bands is what makes that legible - otherwise a switch
                  called "core-01" sitting in the distribution band reads as a
                  bug rather than as the answer. */}
              {[...bands.entries()].map(([tier, y]) => (
                <line
                  key={`band-${tier}`}
                  x1={-4000}
                  x2={4000}
                  y1={y}
                  y2={y}
                  stroke="#cbd5e1"
                  strokeWidth={1}
                  strokeDasharray="2 6"
                  opacity={0.7}
                  pointerEvents="none"
                />
              ))}

              {visibleLinks.map((link) => {
                const source = placed.get(link.source);
                const target = placed.get(link.target);
                if (!source || !target) return null;

                const touched = focus === link.source || focus === link.target;
                const faded =
                  focusedKeys && !touched ? 0.12 : link.hidden ? 0.3 : 0.9;

                // A gentle curve rather than a straight line: two devices with
                // more than one cable between them would otherwise draw on top
                // of each other, and curves read better in a dense diagram.
                const midX = (source.x + target.x) / 2;
                const midY = (source.y + target.y) / 2;
                const dx = target.x - source.x;
                const dy = target.y - source.y;
                const bow = Math.min(28, Math.hypot(dx, dy) * 0.12);
                const length = Math.hypot(dx, dy) || 1;
                const controlX = midX - (dy / length) * bow;
                const controlY = midY + (dx / length) * bow;

                return (
                  <g key={link.key} data-link-key={link.key}>
                    <path
                      d={`M ${source.x} ${source.y} Q ${controlX} ${controlY} ${target.x} ${target.y}`}
                      fill="none"
                      stroke={
                        link.manual
                          ? '#a855f7'
                          : touched
                          ? '#2563eb'
                          : link.confirmed_both_ends
                          ? '#94a3b8'
                          : '#cbd5e1'
                      }
                      strokeWidth={touched ? 2.5 : link.confirmed_both_ends ? 1.8 : 1.2}
                      strokeDasharray={link.manual ? '6 4' : undefined}
                      opacity={faded}
                    />
                    {touched && (link.source_interface || link.target_interface) && (
                      <text
                        x={controlX}
                        y={controlY - 5}
                        textAnchor="middle"
                        fontSize={10}
                        className="fill-gray-600"
                      >
                        {link.source_interface}
                        {link.target_interface ? ` ↔ ${link.target_interface}` : ''}
                      </text>
                    )}
                  </g>
                );
              })}

              {visibleNodes.map((node) => {
                const spot = placed.get(node.key);
                if (!spot) return null;

                const radius = nodeRadius(node);
                const colour = nodeColour(node);
                const hosts = node.host_count ?? 0;
                const drilled = expanded.includes(node.key);
                const isSelected = selected === node.key;
                const opacity = node.hidden ? 0.35 : dim(node.key);

                return (
                  <g
                    key={node.key}
                    data-node-key={node.key}
                    data-tier={tierOf(node)}
                    data-kind={node.kind ?? 'unknown'}
                    transform={`translate(${spot.x}, ${spot.y})`}
                    onMouseDown={(event) => {
                      event.stopPropagation();
                      if (canEdit) setDragging(node.key);
                    }}
                    onMouseEnter={() => setHovered(node.key)}
                    onMouseLeave={() => setHovered(null)}
                    onClick={() => handleNodeClick(node.key)}
                    className={canEdit ? 'cursor-move' : 'cursor-pointer'}
                    opacity={opacity}
                  >
                    {/* A halo picks the selection out without moving anything */}
                    {isSelected && (
                      <circle r={radius + 7} fill={colour} opacity={0.18} />
                    )}

                    <circle
                      r={radius}
                      fill="#ffffff"
                      stroke={colour}
                      strokeWidth={isSelected ? 3 : 2}
                      strokeDasharray={node.managed ? undefined : '5 3'}
                      filter="url(#node-shadow)"
                    />

                    <DeviceIcon
                      kind={node.kind}
                      colour={colour}
                      scale={radius / 26}
                    />

                    <text
                      y={radius + 15}
                      textAnchor="middle"
                      fontSize={11}
                      className="fill-gray-900 font-medium"
                      style={{ paintOrder: 'stroke', stroke: '#f8fafc', strokeWidth: 3 }}
                    >
                      {node.label}
                    </text>

                    {/* Link count, on the rim rather than over the icon */}
                    {node.link_count > 0 && (
                      <g transform={`translate(${-radius + 4}, ${-radius + 4})`}>
                        <circle r={8} fill={colour} />
                        <text
                          y={3}
                          textAnchor="middle"
                          fontSize={9}
                          className="fill-white font-semibold"
                        >
                          {node.link_count}
                        </text>
                      </g>
                    )}

                    {/* How many end hosts are behind this node, so the
                        drill-down is discoverable without expanding it. */}
                    {hosts > 0 && !showingHosts && (
                      <g
                        onClick={(event) => {
                          event.stopPropagation();
                          toggleExpanded(node.key);
                        }}
                        onMouseDown={(event) => event.stopPropagation()}
                        data-testid={`drill-${node.key}`}
                        className="cursor-pointer"
                        transform={`translate(${radius - 2}, ${-radius + 2})`}
                      >
                        <circle
                          r={10}
                          fill={drilled ? '#7c3aed' : '#ffffff'}
                          stroke={drilled ? '#7c3aed' : '#94a3b8'}
                          strokeWidth={1.5}
                        />
                        <text
                          y={3.5}
                          textAnchor="middle"
                          fontSize={9}
                          className={
                            drilled
                              ? 'fill-white font-semibold'
                              : 'fill-gray-700 font-semibold'
                          }
                        >
                          {drilled ? '−' : hosts > 99 ? '99+' : hosts}
                        </text>
                      </g>
                    )}
                  </g>
                );
              })}
            </g>

            {/* Band labels in screen space: pinned to the left edge whatever
                the pan, and skipped when a band has scrolled out of view. */}
            {[...bands.entries()].map(([tier, y]) => {
              const screenY = y * view.k + view.y;
              if (screenY < 16 || screenY > size.height - 8) return null;

              return (
                <text
                  key={`band-label-${tier}`}
                  x={12}
                  y={screenY - 6}
                  fontSize={10}
                  className="fill-slate-400 font-semibold uppercase"
                  style={{ letterSpacing: '0.08em' }}
                  pointerEvents="none"
                >
                  {TIER_LABELS[tier as Tier] ?? tier}
                </text>
              );
            })}
          </svg>
        )}

        {/* Zoom controls, bottom right */}
        <div className="absolute bottom-4 right-4 flex flex-col gap-1">
          <button
            onClick={() => zoomBy(1.25)}
            data-testid="zoom-in"
            className="h-8 w-8 flex items-center justify-center bg-white border border-gray-300 rounded shadow-sm hover:bg-gray-50"
            title="Zoom in"
          >
            <Plus className="h-4 w-4" />
          </button>
          <button
            onClick={() => zoomBy(1 / 1.25)}
            data-testid="zoom-out"
            className="h-8 w-8 flex items-center justify-center bg-white border border-gray-300 rounded shadow-sm hover:bg-gray-50"
            title="Zoom out"
          >
            <Minus className="h-4 w-4" />
          </button>
          <button
            onClick={resetView}
            className="h-8 w-8 flex items-center justify-center bg-white border border-gray-300 rounded shadow-sm hover:bg-gray-50"
            title="Reset the view"
          >
            <Crosshair className="h-4 w-4" />
          </button>
        </div>

        {/* Legend, bottom left */}
        <div className="absolute bottom-4 left-4 bg-white/90 backdrop-blur border border-gray-200 rounded-lg shadow-sm px-3 py-2 text-xs text-gray-600 max-w-[min(38rem,calc(100%-6rem))]">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            {LEGEND_KINDS.map((kind) => (
              <span key={kind} className="inline-flex items-center gap-1.5">
                <DeviceKindBadge kind={kind} colour="#475569" />
                {KIND_LABELS[kind]}
              </span>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-1.5 pt-1.5 border-t border-gray-100">
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-emerald-600" />
              Backed up
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-red-600" />
              Backup failed
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-amber-600" />
              No working login
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-slate-500" />
              Unmanaged
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="inline-block h-0 w-5 border-t-2 border-dashed border-purple-500" />
              Hand-drawn
            </span>
          </div>
        </div>

        {/* Counts, top left */}
        <div className="absolute top-3 left-4 flex items-center gap-3 text-xs text-gray-600 bg-white/90 backdrop-blur border border-gray-200 rounded-full px-3 py-1.5 shadow-sm">
          <span>{graph?.stats.managed_nodes ?? 0} managed</span>
          <span>{graph?.stats.unmanaged_nodes ?? 0} unmanaged</span>
          <span>{visibleLinks.length} links</span>
          {(graph?.stats.hidden_hosts ?? 0) > 0 && !showingHosts && (
            <span className="text-purple-700">
              {graph?.stats.hidden_hosts} hosts folded
            </span>
          )}
        </div>

        {/* Layers and filters, as an overlay so the diagram keeps the space */}
        {showControls && (
          <div className="absolute top-14 left-4 w-80 bg-white border border-gray-200 rounded-lg shadow-lg p-4 space-y-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="inline-flex items-center gap-2 font-medium text-gray-900">
                <Layers className="h-4 w-4 text-gray-400" />
                Layers
              </span>
              <button
                onClick={() => setShowControls(false)}
                className="text-gray-400 hover:text-gray-600"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="flex flex-wrap gap-1.5">
              {ALL_TIERS.map((tier) => {
                const on = tiers.includes(tier);
                return (
                  <button
                    key={tier}
                    onClick={() => toggleTier(tier)}
                    data-testid={`tier-${tier}`}
                    title={TIER_HINTS[tier]}
                    className={`px-2.5 py-1 rounded-full text-xs border ${
                      on
                        ? 'border-blue-300 bg-blue-50 text-blue-800'
                        : 'border-gray-300 text-gray-600 hover:bg-gray-50'
                    }`}
                  >
                    {TIER_LABELS[tier]}
                  </button>
                );
              })}
            </div>

            {expanded.length > 0 && (
              <button
                onClick={() => setExpanded([])}
                className="w-full px-2 py-1.5 rounded text-xs border border-purple-300 bg-purple-50 text-purple-800 hover:bg-purple-100"
              >
                Fold away {expanded.length} drill-down
                {expanded.length === 1 ? '' : 's'}
              </button>
            )}

            <div className="space-y-2 pt-2 border-t border-gray-100">
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
            </div>

            {!showingHosts && (
              <p className="text-xs text-gray-500 flex items-start gap-1.5 pt-2 border-t border-gray-100">
                <Users className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                End hosts are folded away. Click the count on a node to unfold
                just that one.
              </p>
            )}
          </div>
        )}

        {/* Drawing a link: say so, and offer a way out */}
        {linkFrom && (
          <div className="absolute top-3 left-1/2 -translate-x-1/2 bg-blue-600 text-white rounded-full px-4 py-1.5 text-sm shadow-lg flex items-center gap-3">
            <span>
              Pick the other end of the link from{' '}
              <strong>{nodesByKey.get(linkFrom)?.label}</strong>
            </span>
            <button onClick={() => setLinkFrom(null)} className="hover:text-blue-100">
              <X className="h-4 w-4" />
            </button>
          </div>
        )}

        {/* Details, as a drawer over the diagram rather than a column beside it */}
        {selectedNode && (
          <div
            data-testid="node-details"
            className="absolute top-3 right-3 bottom-3 w-80 bg-white border border-gray-200 rounded-lg shadow-xl flex flex-col"
          >
            <div className="p-4 border-b border-gray-100">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <h3 className="font-semibold text-gray-900 break-words flex items-center gap-2">
                    <DeviceKindBadge
                      kind={selectedNode.kind}
                      colour={nodeColour(selectedNode)}
                    />
                    {selectedNode.label}
                  </h3>
                  <p className="text-xs text-gray-500 mt-1">
                    {KIND_LABELS[(selectedNode.kind as DeviceKind) ?? 'unknown']}
                    {' · '}
                    {selectedNode.managed ? 'Managed' : 'Unmanaged neighbour'}
                    {' · '}
                    {TIER_LABELS[tierOf(selectedNode)]}
                  </p>
                </div>
                <button
                  onClick={() => setSelected(null)}
                  className="text-gray-400 hover:text-gray-600 shrink-0"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {(selectedNode.host_count ?? 0) > 0 && !showingHosts && (
                <button
                  onClick={() => toggleExpanded(selectedNode.key)}
                  className="w-full inline-flex items-center justify-center px-3 py-2 border border-purple-300 bg-purple-50 text-purple-800 rounded text-sm hover:bg-purple-100"
                >
                  <Users className="h-4 w-4 mr-2" />
                  {expanded.includes(selectedNode.key)
                    ? 'Fold its hosts away'
                    : `Show its ${selectedNode.host_count} end host${
                        selectedNode.host_count === 1 ? '' : 's'
                      }`}
                </button>
              )}

              <dl className="text-sm space-y-1">
                {selectedNode.ip_address && (
                  <div className="flex justify-between gap-2">
                    <dt className="text-gray-500">Address</dt>
                    <dd className="text-gray-900">{selectedNode.ip_address}</dd>
                  </div>
                )}
                {selectedNode.model && (
                  <div className="flex justify-between gap-2">
                    <dt className="text-gray-500">Model</dt>
                    <dd className="text-gray-900 text-right">{selectedNode.model}</dd>
                  </div>
                )}
                {selectedNode.device_type && (
                  <div className="flex justify-between gap-2">
                    <dt className="text-gray-500">Platform</dt>
                    <dd className="text-gray-900">{selectedNode.device_type}</dd>
                  </div>
                )}
                <div className="flex justify-between gap-2">
                  <dt className="text-gray-500">Links</dt>
                  <dd className="text-gray-900">{selectedLinks.length}</dd>
                </div>
                {selectedNode.platform && (
                  <div className="text-gray-600 text-xs pt-1 break-words">
                    {selectedNode.platform}
                  </div>
                )}
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
                <ul className="space-y-2">
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
                        className="text-sm border border-gray-100 rounded p-2 hover:border-gray-300"
                        onMouseEnter={() => setHovered(otherKey)}
                        onMouseLeave={() => setHovered(null)}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <button
                            className="min-w-0 text-left"
                            onClick={() => setSelected(otherKey)}
                          >
                            <p className="font-medium text-gray-900 truncate flex items-center gap-1.5">
                              <DeviceKindBadge
                                kind={other?.kind}
                                colour={other ? nodeColour(other) : '#64748b'}
                              />
                              {other?.label ?? otherKey}
                            </p>
                            <p className="text-xs text-gray-500">
                              {localPort || 'unknown port'} ·{' '}
                              {link.manual ? 'hand-drawn' : link.protocol.toUpperCase()}
                              {link.hidden ? ' · hidden' : ''}
                            </p>
                          </button>
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
          </div>
        )}
      </div>
    </div>
  );
};
