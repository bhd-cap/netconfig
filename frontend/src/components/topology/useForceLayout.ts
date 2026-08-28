/**
 * The physics behind the topology diagram.
 *
 * A pure force layout produces a hairball: readable as a blob, useless for
 * answering "what is upstream of this switch". A pure hierarchy produces a
 * rigid tree that hides the cross-links a real network is full of. This runs a
 * force simulation with a per-tier Y target, so the core settles at the top and
 * access switches below it, while the links pull related devices together
 * horizontally - layered, but organic, and it settles rather than drifting.
 *
 * Three rules the rest of the page depends on:
 *
 * - A node the saved diagram places is pinned. Somebody arranged that on
 *   purpose and a simulation must not shuffle it back.
 * - A node being dragged is pinned to the cursor while the rest reflows
 *   around it, which is what makes the diagram feel alive.
 * - The simulation stops when it settles. Leaving it running burns a core and
 *   makes the picture crawl for no reason.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type Simulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from 'd3-force';

export interface LayoutNode extends SimulationNodeDatum {
  key: string;
  tier: string;
  /** Radius used for collision, so big nodes claim more room. */
  radius: number;
  /** Set when the saved diagram placed it: the simulation leaves it alone. */
  pinned?: boolean;
}

export interface LayoutLink extends SimulationLinkDatum<LayoutNode> {
  source: string | LayoutNode;
  target: string | LayoutNode;
}

/** Top of the diagram downward. Anything unrecognised sits with the hosts. */
const TIER_ORDER = ['core', 'distribution', 'access', 'edge', 'host'];

export interface LayoutOptions {
  width: number;
  height: number;
  /** Restart the simulation when this changes - a filter or drill-down. */
  signature: string;
}

/**
 * The Y a tier settles at, as a fraction of the canvas
 *
 * Only the tiers actually present are spread out, so a diagram of nothing but
 * access switches uses the whole canvas instead of huddling in one band.
 */
function tierBands(tiers: Set<string>, height: number): Map<string, number> {
  const present = TIER_ORDER.filter((tier) => tiers.has(tier));
  const bands = new Map<string, number>();

  if (present.length === 0) return bands;
  if (present.length === 1) {
    bands.set(present[0], height / 2);
    return bands;
  }

  // Margins top and bottom so the outermost tiers are not against the edge.
  const top = height * 0.14;
  const usable = height * 0.72;

  present.forEach((tier, index) => {
    bands.set(tier, top + (usable * index) / (present.length - 1));
  });

  return bands;
}

export function useForceLayout(
  inputNodes: LayoutNode[],
  inputLinks: LayoutLink[],
  { width, height, signature }: LayoutOptions
) {
  const simulationRef = useRef<Simulation<LayoutNode, LayoutLink> | null>(null);
  const nodesRef = useRef<LayoutNode[]>([]);

  // A counter rather than the node array: the simulation mutates its nodes in
  // place, so a new array identity every tick would defeat React's diffing
  // while still re-rendering. The consumer reads positions through
  // `positions()` when this changes.
  const [tick, setTick] = useState(0);
  const [running, setRunning] = useState(false);

  const bands = useMemo(
    () => tierBands(new Set(inputNodes.map((node) => node.tier)), height),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [signature, height]
  );

  useEffect(() => {
    if (!inputNodes.length) {
      simulationRef.current?.stop();
      simulationRef.current = null;
      nodesRef.current = [];
      setTick((value) => value + 1);
      return;
    }

    // Carry over the positions of nodes that were already on screen, so a
    // filter change nudges the picture rather than throwing it in the air.
    const previous = new Map(nodesRef.current.map((node) => [node.key, node]));

    const nodes: LayoutNode[] = inputNodes.map((node) => {
      const old = previous.get(node.key);
      const band = bands.get(node.tier) ?? height / 2;

      return {
        ...node,
        x: node.x ?? old?.x ?? width / 2 + (Math.random() - 0.5) * width * 0.6,
        y: node.y ?? old?.y ?? band + (Math.random() - 0.5) * 60,
        vx: old?.vx ?? 0,
        vy: old?.vy ?? 0,
        // A pinned node is fixed outright: d3 honours fx/fy absolutely.
        fx: node.pinned ? node.x : undefined,
        fy: node.pinned ? node.y : undefined,
      };
    });

    const byKey = new Map(nodes.map((node) => [node.key, node]));
    const links = inputLinks.filter(
      (link) =>
        byKey.has(typeof link.source === 'string' ? link.source : link.source.key) &&
        byKey.has(typeof link.target === 'string' ? link.target : link.target.key)
    );

    simulationRef.current?.stop();

    const simulation = forceSimulation<LayoutNode, LayoutLink>(nodes)
      .force(
        'link',
        forceLink<LayoutNode, LayoutLink>(links)
          .id((node) => node.key)
          // Longer links between tiers than within one, so the bands stay
          // legible instead of being pulled into each other.
          .distance((link) => {
            const source = link.source as LayoutNode;
            const target = link.target as LayoutNode;
            return source.tier === target.tier ? 130 : 170;
          })
          .strength(0.3)
      )
      // Repulsion, capped so one very well-connected node cannot fling the
      // rest off the canvas.
      .force('charge', forceManyBody<LayoutNode>().strength(-900).distanceMax(900))
      // The tier band: strong enough to hold the layers, weak enough that a
      // link can bend one.
      .force(
        'tier',
        forceY<LayoutNode>((node) => bands.get(node.tier) ?? height / 2).strength(0.55)
      )
      // Weak, and only to stop the graph wandering off: the fit-to-view in
      // the page is what decides where it ends up on screen.
      .force('centre', forceX<LayoutNode>(width / 2).strength(0.02))
      .force(
        'collide',
        forceCollide<LayoutNode>((node) => node.radius + 14).strength(0.9)
      )
      .alpha(0.9)
      .alphaDecay(0.045);

    simulation.on('tick', () => {
      // Keep everything inside the canvas: a node that drifts off is a node
      // nobody can find.
      for (const node of nodes) {
        const margin = node.radius + 8;
        node.x = Math.max(margin, Math.min(width - margin, node.x ?? 0));
        node.y = Math.max(margin, Math.min(height - margin, node.y ?? 0));
      }
      setTick((value) => value + 1);
    });

    simulation.on('end', () => setRunning(false));

    simulationRef.current = simulation;
    nodesRef.current = nodes;
    setRunning(true);

    return () => {
      simulation.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature, width, height]);

  /** Current positions, keyed for the renderer. */
  const positions = useCallback(() => {
    const map = new Map<string, { x: number; y: number }>();
    for (const node of nodesRef.current) {
      map.set(node.key, { x: node.x ?? 0, y: node.y ?? 0 });
    }
    return map;
  }, []);

  /** Pin a node to the cursor and let the rest reflow around it. */
  const dragTo = useCallback((key: string, x: number, y: number) => {
    const node = nodesRef.current.find((candidate) => candidate.key === key);
    if (!node) return;

    node.fx = x;
    node.fy = y;
    node.x = x;
    node.y = y;

    const simulation = simulationRef.current;
    if (simulation && simulation.alpha() < 0.2) {
      simulation.alpha(0.35).restart();
      setRunning(true);
    }
  }, []);

  /**
   * Release a dragged node
   *
   * `keep` leaves it pinned where it was dropped, which is what a person
   * dragging a node into place means; without it the node rejoins the
   * simulation and drifts back.
   */
  const endDrag = useCallback((key: string, keep = true) => {
    const node = nodesRef.current.find((candidate) => candidate.key === key);
    if (!node) return;

    if (!keep) {
      node.fx = undefined;
      node.fy = undefined;
    }
  }, []);

  /** Unpin everything and shake the layout out again. */
  const reheat = useCallback(() => {
    for (const node of nodesRef.current) {
      node.fx = undefined;
      node.fy = undefined;
    }
    simulationRef.current?.alpha(0.9).restart();
    setRunning(true);
  }, []);

  // The Y each tier settled at, so the page can label the bands. Without
  // them the vertical arrangement is unexplained, and a device sitting
  // lower than its name suggests looks like a bug rather than what it is:
  // tiers come from how many other infrastructure nodes a device links to.
  return { positions, dragTo, endDrag, reheat, running, tick, bands };
}
