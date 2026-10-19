/*
  Destination chips on the stand-in approval card (owner decision 2026-10-19):
  the officer ticks which destinations receive the approved bytes. Text chips
  only, never platform logos.

  A pipeline run may carry the list under `destinations`; then the card reads
  it. The hand-written sample does not, so the card shows the six chips below
  and says in the same sentence that they are a design preview.
*/
import type { Destination, DestinationId, Run } from "./types";
import { PREVIEW_ONLY } from "./runs";

export const DESTINATION_PREVIEW: Destination[] = [
  { id: "club_site", label: "Club website (CMS feed)", status: "selected", preview: false },
  { id: "feeds", label: "RSS and JSON Feed", status: "selected", preview: false },
  { id: "instagram", label: "Instagram", status: "not_posted", preview: true },
  { id: "youtube", label: "YouTube", status: "not_posted", preview: true },
  { id: "newsroom_teams", label: "Internal newsroom (Teams)", status: "selected", preview: false },
  { id: "journalist_email", label: "Journalist email digest", status: "not_selected", preview: false },
];

export const PREVIEW_ONLY_SENTENCE = "Preview: not posted; posting needs Meta and Google app approval.";

export const DESIGN_PREVIEW_DESTINATIONS_SENTENCE =
  "Design preview of the destination chips: this run carries no destinations list, so these six are a fixed preview, and ticking them sends nothing anywhere.";

export const RUN_DESTINATIONS_SENTENCE = "These destinations are read from the run file.";

export interface DestinationChoice {
  destinations: Destination[];
  fromRun: boolean;
}

export function destinationsFor(run: Run): DestinationChoice {
  if (Array.isArray(run.destinations) && run.destinations.length > 0) {
    return { destinations: run.destinations, fromRun: true };
  }
  return { destinations: DESTINATION_PREVIEW, fromRun: false };
}

export function isPreviewOnly(id: DestinationId): boolean {
  return PREVIEW_ONLY.includes(id);
}

/* Ticked at first: whatever the run (or the preview) marks as selected or done. */
export function initiallyTicked(destinations: Destination[]): DestinationId[] {
  return destinations
    .filter((d) => d.status === "selected" || d.status === "published" || d.status === "sent")
    .map((d) => d.id);
}
