/*
  The marks that say whose phase it is: a side badge (Home or Away, with a
  triangle that points up for home and down for away, the same way the
  timeline stacks them), a crest drawn from the club's kit colours, and one
  drawn icon per event type. Meaning never rests on colour alone: the word,
  the triangle and the icon shape carry it too. All original drawings, no
  league, club or broadcaster mark.
*/
import { useId } from "react";
import { cn } from "../../lib/cn";
import { SIDE_WORD, type MatchClub, type Side } from "../../lib/match";

export const SIDE_TEXT: Record<Side, string> = { home: "text-home-text", away: "text-away-text" };
export const SIDE_BG: Record<Side, string> = { home: "bg-home-bg", away: "bg-away-bg" };
export const SIDE_FILL: Record<Side, string> = { home: "fill-home", away: "fill-away" };
export const SIDE_STROKE: Record<Side, string> = { home: "stroke-home", away: "stroke-away" };

export function SideTriangle({ side, className }: { side: Side; className?: string }) {
  return (
    <svg viewBox="0 0 10 10" className={cn("size-2.5 shrink-0", className)} aria-hidden="true" focusable="false">
      <path d={side === "home" ? "M5 1.5 9 8.5H1z" : "M5 8.5 1 1.5h8z"} className="fill-current" />
    </svg>
  );
}

/* "Home" or "Away" in the side's colour pair; compact shows H or A and keeps the word for screen readers. */
export function SideBadge({ side, compact = false, className }: { side: Side; compact?: boolean; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full px-1.5 py-px text-[0.6875rem] leading-4 font-semibold tracking-wide uppercase",
        SIDE_BG[side],
        SIDE_TEXT[side],
        className,
      )}
    >
      <SideTriangle side={side} />
      {compact ? (
        <>
          <span aria-hidden="true">{side === "home" ? "H" : "A"}</span>
          <span className="sr-only">{SIDE_WORD[side]}</span>
        </>
      ) : (
        SIDE_WORD[side]
      )}
    </span>
  );
}

/* A shield in the kit's two colours with a sash; decorative, the club name is always beside it. */
export function Crest({ club, className }: { club: Pick<MatchClub, "kit_primary_hex" | "kit_secondary_hex"> | null; className?: string }) {
  const clip = `crest-${useId().replace(/[^A-Za-z0-9_-]/g, "")}`;
  const shield = "M16 2.5 27.5 6v9.2c0 7.2-4.9 12.3-11.5 14.8C9.4 27.5 4.5 22.4 4.5 15.2V6z";
  return (
    <svg viewBox="0 0 32 32" className={cn("size-8 shrink-0", className)} aria-hidden="true" focusable="false">
      <defs>
        <clipPath id={clip}>
          <path d={shield} />
        </clipPath>
      </defs>
      {club ? (
        <g clipPath={`url(#${clip})`}>
          <rect x="0" y="0" width="32" height="32" fill={club.kit_primary_hex} />
          <path d="M0 21 32 7v7L0 28z" fill={club.kit_secondary_hex} />
          <circle cx="16" cy="11" r="3" fill={club.kit_secondary_hex} />
        </g>
      ) : (
        <path d={shield} className="fill-surface-3" />
      )}
      <path d={shield} className="fill-none stroke-fg" strokeWidth="1.4" strokeLinejoin="round" />
    </svg>
  );
}

export type IconKind =
  | "goal"
  | "shot_on"
  | "shot_off"
  | "save"
  | "block"
  | "yellow"
  | "red"
  | "sub"
  | "corner"
  | "foul"
  | "offside"
  | "big_chance";

/* 24 by 24, drawn in currentColor; cards carry their own fill with an outline in currentColor. */
export function EventIcon({ kind, className }: { kind: IconKind; className?: string }) {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  return (
    <svg viewBox="0 0 24 24" className={cn("size-4 shrink-0", className)} aria-hidden="true" focusable="false">
      {kind === "goal" ? (
        <g {...common}>
          <path d="M2.5 19V5.5h19V19" />
          <path d="M2.5 9.5h19M2.5 13.5h19M7.5 5.5V19M12 5.5V19M16.5 5.5V19" strokeWidth="0.8" opacity="0.55" />
          <circle cx="12" cy="16" r="3.6" className="fill-surface" />
          <path d="m12 14.3 1.6 1.2-.6 1.9h-2l-.6-1.9z" fill="currentColor" strokeWidth="0.6" />
        </g>
      ) : null}
      {kind === "shot_on" ? (
        <g {...common}>
          <circle cx="12" cy="12" r="8.5" />
          <circle cx="12" cy="12" r="4.5" />
          <circle cx="12" cy="12" r="1.2" fill="currentColor" />
        </g>
      ) : null}
      {kind === "shot_off" ? (
        <g {...common}>
          <path d="M4 20V8h9" />
          <path d="M5 18c4-1 9-5 14.5-12.5" />
          <path d="m15.5 5.2 4.2.3-.6 4" />
        </g>
      ) : null}
      {kind === "save" ? (
        <g {...common}>
          <path d="M8 21v-3.5L5 13V8.5a1.5 1.5 0 0 1 3 0V11" />
          <path d="M8 11V5a1.5 1.5 0 0 1 3 0v5.5M11 10.5V4a1.5 1.5 0 0 1 3 0v6.5M14 10.5V5.5a1.5 1.5 0 0 1 3 0V14c0 2.5-1.5 4-3 5v2" />
        </g>
      ) : null}
      {kind === "block" ? (
        <g {...common}>
          <path d="M12 2.8 19.5 6v5.5c0 4.6-3.2 8.2-7.5 9.9-4.3-1.7-7.5-5.3-7.5-9.9V6z" />
          <path d="M8.5 12.5h7" />
        </g>
      ) : null}
      {kind === "yellow" || kind === "red" ? (
        <g>
          <rect
            x="6.5"
            y="3"
            width="11"
            height="16"
            rx="1.6"
            transform="rotate(12 12 11)"
            className={kind === "yellow" ? "fill-pressure" : "fill-danger"}
            stroke="currentColor"
            strokeWidth="1.6"
          />
        </g>
      ) : null}
      {kind === "sub" ? (
        <g {...common}>
          <path d="M8 20V5M4.5 8.5 8 5l3.5 3.5" />
          <path d="M16 4v15M12.5 15.5 16 19l3.5-3.5" />
        </g>
      ) : null}
      {kind === "corner" ? (
        <g {...common}>
          <path d="M5 21V3" />
          <path d="M5 3.5h11l-3 3.5 3 3.5H5" fill="currentColor" fillOpacity="0.25" />
          <path d="M5 21a6 6 0 0 1 6-6" strokeWidth="1.2" />
        </g>
      ) : null}
      {kind === "foul" ? (
        <g {...common}>
          <circle cx="9" cy="14" r="5.5" />
          <path d="M13.5 10.5 21 7v4.5l-5.6 1.6" />
          <path d="M9 4v3M5.2 5.4l1.6 2.3M12.8 5.4l-1.6 2.3" strokeWidth="1.4" />
        </g>
      ) : null}
      {kind === "offside" ? (
        <g {...common}>
          <path d="M6 21V3" />
          <path d="M6 3.5h12v8H6" />
          <path d="M6 3.5 18 11.5M18 3.5 6 11.5" strokeWidth="1" opacity="0.6" />
        </g>
      ) : null}
      {kind === "big_chance" ? (
        <g {...common}>
          <path d="m12 3 2.6 5.6 6.1.7-4.5 4.2 1.2 6L12 16.6 6.6 19.5l1.2-6L3.3 9.3l6.1-.7z" fill="currentColor" fillOpacity="0.2" />
        </g>
      ) : null}
    </svg>
  );
}

export const ICON_FOR: Record<string, IconKind> = {
  goal: "goal",
  save: "save",
  block: "block",
  shot_off: "shot_off",
  yellow: "yellow",
  red: "red",
  sub: "sub",
  corner: "corner",
  foul: "foul",
};
