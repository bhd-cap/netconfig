/**
 * Device glyphs for the topology diagram.
 *
 * Drawn as inline SVG paths rather than an icon font or <image>, because they
 * live inside the diagram's own coordinate space: they have to scale with the
 * zoom, inherit the node's colour, and stay crisp at any size. Each is drawn
 * on a 24x24 grid centred on the origin so a node can place one with a single
 * translate.
 *
 * Every glyph is built from three layers, and the split matters. `body` is
 * filled in the node's colour. `detail` is drawn *on top of* that body, so it
 * has to be light or it disappears into it - which is exactly what happened
 * when the arrows on a switch were drawn in the same colour as the switch.
 * `outline` is drawn outside the body, against the node's white disc, so it
 * takes the colour.
 *
 * The shapes follow the conventions a network engineer already reads without a
 * legend - a router is a puck with opposing arrows, a switch is a flat box with
 * parallel arrows, a firewall is a brick wall - so the diagram is scannable at
 * a glance rather than requiring the labels to be read one at a time.
 */
import React from 'react';

export type DeviceKind =
  | 'router'
  | 'switch'
  | 'firewall'
  | 'wireless'
  | 'server'
  | 'phone'
  | 'printer'
  | 'host'
  | 'unknown';

export const KIND_LABELS: Record<DeviceKind, string> = {
  router: 'Router',
  switch: 'Switch',
  firewall: 'Firewall',
  wireless: 'Wireless AP',
  server: 'Server',
  phone: 'IP phone',
  printer: 'Printer',
  host: 'End device',
  unknown: 'Unidentified',
};

interface Glyph {
  /** Filled in the node's colour. */
  body?: string[];
  /** Stroked in white, over the body. */
  detail?: string[];
  /** Stroked in the node's colour, outside the body. */
  outline?: string[];
}

const GLYPHS: Record<DeviceKind, Glyph> = {
  // A puck seen at a slight angle, with traffic crossing it both ways.
  router: {
    body: [
      'M -11 -4 a 11 4.5 0 0 1 22 0 v 5 a 11 4.5 0 0 1 -22 0 Z',
    ],
    detail: [
      'M -5.5 -4 h 7 m -2.5 -2.5 l 2.5 2.5 l -2.5 2.5',
      'M 5.5 1.5 h -7 m 2.5 -2.5 l -2.5 2.5 l 2.5 2.5',
    ],
  },
  // A flat box with frames going both ways across it.
  switch: {
    body: [
      'M -12 -6 h 24 a 2 2 0 0 1 2 2 v 8 a 2 2 0 0 1 -2 2 h -24 a 2 2 0 0 1 -2 -2 v -8 a 2 2 0 0 1 2 -2 Z',
    ],
    detail: [
      'M -7 -2 h 7 m -2.5 -2.5 l 2.5 2.5 l -2.5 2.5',
      'M 7 4 h -7 m 2.5 -2.5 l -2.5 2.5 l 2.5 2.5',
    ],
  },
  // A brick wall.
  firewall: {
    body: ['M -12 -9 h 24 v 18 h -24 Z'],
    detail: [
      'M -12 -3 h 24 M -12 3 h 24',
      'M -4 -9 v 6 M 4 -3 v 6 M -4 3 v 6',
    ],
  },
  // An access point radiating upward: the arcs sit above the body, so they
  // are drawn against the white disc and take the colour.
  wireless: {
    body: ['M -7 3 h 14 a 1.5 1.5 0 0 1 1.5 1.5 v 4 a 1.5 1.5 0 0 1 -1.5 1.5 h -14 a 1.5 1.5 0 0 1 -1.5 -1.5 v -4 a 1.5 1.5 0 0 1 1.5 -1.5 Z'],
    detail: ['M -4 6.5 h 8'],
    outline: [
      'M -4 -1 a 6 6 0 0 1 8 0',
      'M -8 -5 a 12 12 0 0 1 16 0',
    ],
  },
  // A two-unit chassis.
  server: {
    body: [
      'M -9 -10 h 18 v 8 h -18 Z',
      'M -9 1 h 18 v 8 h -18 Z',
    ],
    detail: [
      'M -6 -6 h 4 M 6 -6 h 1',
      'M -6 5 h 4 M 6 5 h 1',
    ],
  },
  // A desk phone: the handset arcs above the base.
  phone: {
    body: ['M -9 1 h 18 a 2 2 0 0 1 2 2 v 5 a 2 2 0 0 1 -2 2 h -18 a 2 2 0 0 1 -2 -2 v -5 a 2 2 0 0 1 2 -2 Z'],
    detail: ['M -5 5.5 h 10'],
    outline: ['M -7 -2 a 7 7 0 0 1 14 0'],
  },
  // Paper feeding out of the top.
  printer: {
    body: ['M -10 -2 h 20 a 2 2 0 0 1 2 2 v 6 a 2 2 0 0 1 -2 2 h -20 a 2 2 0 0 1 -2 -2 v -6 a 2 2 0 0 1 2 -2 Z'],
    detail: ['M -7 1.5 h 4', 'M 7 1.5 h 1'],
    outline: ['M -6 -2 v -7 h 12 v 7'],
  },
  // A monitor on a stand.
  host: {
    body: ['M -10 -8 h 20 a 1.5 1.5 0 0 1 1.5 1.5 v 11 a 1.5 1.5 0 0 1 -1.5 1.5 h -20 a 1.5 1.5 0 0 1 -1.5 -1.5 v -11 a 1.5 1.5 0 0 1 1.5 -1.5 Z'],
    detail: ['M -6.5 -4 h 13 M -6.5 0 h 8'],
    outline: ['M 0 6 v 3 M -5 9 h 10'],
  },
  // A question mark. Nothing identified it, and drawing a switch would be a
  // claim the data does not support.
  unknown: {
    outline: [
      'M -3.5 -3 a 3.5 3.5 0 0 1 6.4 1.9 c 0 2.3 -2.9 2.8 -2.9 5.1',
      'M 0 7 v 0.5',
      'M 0 0 m -10 0 a 10 10 0 1 0 20 0 a 10 10 0 1 0 -20 0',
    ],
  },
};

interface DeviceIconProps {
  kind?: string | null;
  /** Icon colour; the body is filled with it and the outline stroked in it. */
  colour: string;
  /** 1 draws on the native 24x24 grid. */
  scale?: number;
  /** The colour detail is drawn in, over the body. */
  detailColour?: string;
}

/**
 * One device glyph, centred on the origin
 *
 * Meant to be placed inside a <g transform="translate(x y)"> by the caller,
 * which is how the diagram positions everything else.
 */
export const DeviceIcon: React.FC<DeviceIconProps> = ({
  kind,
  colour,
  scale = 1,
  detailColour = '#ffffff',
}) => {
  const glyph = GLYPHS[(kind as DeviceKind) ?? 'unknown'] ?? GLYPHS.unknown;

  return (
    <g transform={scale === 1 ? undefined : `scale(${scale})`} pointerEvents="none">
      {glyph.body?.map((d, index) => (
        <path key={`b${index}`} d={d} fill={colour} />
      ))}
      {glyph.outline?.map((d, index) => (
        <path
          key={`o${index}`}
          d={d}
          fill="none"
          stroke={colour}
          strokeWidth={1.8}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ))}
      {glyph.detail?.map((d, index) => (
        <path
          key={`d${index}`}
          d={d}
          fill="none"
          stroke={detailColour}
          strokeWidth={1.5}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ))}
    </g>
  );
};

/**
 * The same glyphs at a fixed size, for a legend or a table cell
 *
 * On a light background rather than a coloured body, so the detail strokes
 * are drawn in the background colour to read as cut-outs.
 */
export const DeviceKindBadge: React.FC<{
  kind?: string | null;
  colour?: string;
  background?: string;
}> = ({ kind, colour = 'currentColor', background = '#ffffff' }) => (
  <svg viewBox="-14 -14 28 28" className="h-4 w-4 shrink-0" aria-hidden="true">
    <DeviceIcon kind={kind} colour={colour} detailColour={background} />
  </svg>
);
