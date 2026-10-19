/*
  Destination chips on the stand-in approval card (owner decision 2026-10-19):
  the officer ticks which destinations receive the approved bytes. Text chips
  only, never platform logos.

  A run that carries the `destinations` key (agon.publish.webview, see
  web/runs/README.txt) drives the chips: what the sealed bundle offers, what
  was approved, and what happened to each. The hand-written sample has no such
  key, so the card shows the six registry chips and says in the same sentence
  that they are a design preview.
*/
import type { DestinationId, Run } from "./types";
import { DESTINATION_LABELS, OUTBOX_LABEL, PREVIEW_LABEL, PREVIEW_ONLY } from "./runs";

export const PREVIEW_ONLY_SENTENCE = PREVIEW_LABEL;
export const OUTBOX_SENTENCE = OUTBOX_LABEL;

export const DESIGN_PREVIEW_DESTINATIONS_SENTENCE =
  "Design preview of the destination chips: this run carries no destinations list, so these six are a fixed preview, and ticking them sends nothing anywhere.";

export const RUN_DESTINATIONS_SENTENCE =
  "These destinations are read from the run file: what the sealed bundle offers, what was approved, and what happened to each.";

export interface Chip {
  id: DestinationId;
  label: string;
  offered: boolean;
  ticked: boolean;
  /* The sentence the chip always carries (the preview-only rule), or null. */
  note: string | null;
  /* What happened to this destination, from the run, or null. */
  status: string | null;
}

export interface ChipSet {
  chips: Chip[];
  fromRun: boolean;
}

const DESIGN_PREVIEW_TICKED: DestinationId[] = ["club_site", "feeds", "newsroom_teams"];

export function isPreviewOnly(id: DestinationId): boolean {
  return PREVIEW_ONLY.includes(id);
}

export function chipsFor(run: Run): ChipSet {
  const rows = run.destinations;
  if (Array.isArray(rows) && rows.length === DESTINATION_LABELS.length) {
    return {
      fromRun: true,
      chips: rows.map((row) => {
        let status: string;
        if (row.status === "delivered") {
          status = row.preview?.path ? `Delivered: the approved bytes were written to ${row.preview.path}.` : "Delivered.";
        } else if (row.status === "preview_not_posted") {
          status = row.preview?.note ?? "Approved and rendered, not posted or sent.";
        } else if (row.status === "not_selected") {
          status = "Offered by the sealed bundle, not selected.";
        } else {
          status = "Not offered by the sealed bundle, so it cannot be ticked.";
        }
        return {
          id: row.id,
          label: row.label,
          offered: row.status !== "not_offered",
          ticked: row.status === "delivered" || row.status === "preview_not_posted",
          note: isPreviewOnly(row.id) ? PREVIEW_ONLY_SENTENCE : null,
          status,
        };
      }),
    };
  }
  return {
    fromRun: false,
    chips: DESTINATION_LABELS.map(([id, label]) => ({
      id,
      label,
      offered: true,
      ticked: DESIGN_PREVIEW_TICKED.includes(id),
      note: isPreviewOnly(id) ? PREVIEW_ONLY_SENTENCE : null,
      status: null,
    })),
  };
}
