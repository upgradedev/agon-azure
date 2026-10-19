/*
  The replay pitch, drawn to scale in metres (105 by 68). Every position is an
  SVG attribute (cx, cy, x), never an inline style, so the prerendered page
  passes the strict CSP. The home club attacks to the right in both halves.
*/
import { useId } from "react";

export interface PitchProps {
  ball: { x: number; y: number } | null;
  path: [number, number, number, number] | null;
  pressureLeft: number | null;
  summary: string;
}

const BAND_WIDTH = 17;

export function Pitch({ ball, path, pressureLeft, summary }: PitchProps) {
  const titleId = useId();
  const descId = useId();
  /* Used inside url(#...), so only plain characters. */
  const hatchId = `pitch-hatch-${useId().replace(/[^A-Za-z0-9_-]/g, "")}`;
  const bx = ball ? ball.x.toFixed(2) : "52.50";
  const by = ball ? ball.y.toFixed(2) : "34.00";
  return (
    <figure className="overflow-hidden rounded-lg border border-line">
      <svg viewBox="-3 -3 111 74" role="img" aria-labelledby={`${titleId} ${descId}`} className="block h-auto w-full" focusable="false">
        <title id={titleId}>Replay pitch</title>
        <desc id={descId}>{summary}</desc>
        <defs>
          <pattern id={hatchId} width="2.4" height="2.4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="2.4" className="stroke-pressure" strokeWidth="0.6" />
          </pattern>
        </defs>
        <rect x="-3" y="-3" width="111" height="74" className="fill-grass" />
        {[15, 45, 75].map((x) => (
          <rect key={x} x={x} y="0" width="15" height="68" className="fill-stripe" />
        ))}
        {pressureLeft !== null ? (
          <g>
            <rect x={pressureLeft.toFixed(2)} y="0" width={BAND_WIDTH} height="68" className="fill-pressure opacity-20" />
            <rect x={pressureLeft.toFixed(2)} y="0" width={BAND_WIDTH} height="68" fill={`url(#${hatchId})`} className="opacity-60" />
          </g>
        ) : null}
        <g className="fill-none stroke-pitch-line" strokeWidth="0.35">
          <rect x="0" y="0" width="105" height="68" />
          <line x1="52.5" y1="0" x2="52.5" y2="68" />
          <circle cx="52.5" cy="34" r="9.15" />
          <rect x="0" y="13.84" width="16.5" height="40.32" />
          <rect x="88.5" y="13.84" width="16.5" height="40.32" />
          <rect x="0" y="24.84" width="5.5" height="18.32" />
          <rect x="99.5" y="24.84" width="5.5" height="18.32" />
          <path d="M16.5 26.69A9.15 9.15 0 0 1 16.5 41.31" />
          <path d="M88.5 26.69A9.15 9.15 0 0 0 88.5 41.31" />
          <path d="M1 0A1 1 0 0 1 0 1M104 0A1 1 0 0 0 105 1M0 67A1 1 0 0 1 1 68M105 67A1 1 0 0 0 104 68" />
          <rect x="-1.8" y="30.34" width="1.8" height="7.32" />
          <rect x="105" y="30.34" width="1.8" height="7.32" />
        </g>
        <g className="fill-pitch-line">
          <circle cx="52.5" cy="34" r="0.45" />
          <circle cx="11" cy="34" r="0.45" />
          <circle cx="94" cy="34" r="0.45" />
        </g>
        {path ? (
          <line
            x1={path[0].toFixed(2)}
            y1={path[1].toFixed(2)}
            x2={path[2].toFixed(2)}
            y2={path[3].toFixed(2)}
            className="stroke-ball"
            strokeWidth="0.5"
            strokeDasharray="1.2 0.8"
            strokeLinecap="round"
          />
        ) : null}
        <circle cx={bx} cy={by} r="2.2" className="fill-ball opacity-25" />
        <circle cx={bx} cy={by} r="1.1" className="fill-ball stroke-ball-edge" strokeWidth="0.3" />
      </svg>
    </figure>
  );
}
