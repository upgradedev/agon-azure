/*
  A list of evidence links: a file and line in the public repository, or a
  green CI run. Both open on GitHub in a new tab.
*/
import { ExternalLink, FileText, GitBranch } from "lucide-react";
import { evidenceHref, evidenceText, type EvidenceItem } from "../lib/evidence";

export function EvidenceList({ items }: { items: EvidenceItem[] }) {
  return (
    <ul className="space-y-2">
      {items.map((item) => (
        <li key={evidenceHref(item)}>
          <a
            href={evidenceHref(item)}
            target="_blank"
            rel="noreferrer"
            className="group flex items-start gap-3 rounded-lg border border-line px-3 py-2.5 no-underline transition-colors hover:border-line-strong hover:bg-surface-2"
          >
            {item.kind === "file" ? (
              <FileText className="mt-0.5 size-4 shrink-0 text-muted" aria-hidden="true" />
            ) : (
              <GitBranch className="mt-0.5 size-4 shrink-0 text-muted" aria-hidden="true" />
            )}
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-medium text-fg">{item.label}</span>
              <span className="block font-mono text-xs break-all text-link group-hover:underline">{evidenceText(item)}</span>
            </span>
            <ExternalLink className="mt-0.5 size-3.5 shrink-0 text-muted" aria-hidden="true" />
            <span className="sr-only">, opens GitHub in a new tab</span>
          </a>
        </li>
      ))}
    </ul>
  );
}
