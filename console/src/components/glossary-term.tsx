/*
  A glossary term that opens its definition on click, tap or Enter, on a Radix
  popover (a tooltip would do nothing on a phone).

  No Popover.Trigger: Radix gives it aria-controls pointing at content that is
  not in the prerendered page. The button below sets aria-controls only while
  the popover is open, anchors the popover, and takes focus back on close.
*/
import * as Popover from "@radix-ui/react-popover";
import { useId, useRef, useState, type ReactNode } from "react";
import { GLOSSARY, type GlossaryId } from "../lib/glossary";
import { cn } from "../lib/cn";

export function GlossaryTerm({ id, children, className }: { id: GlossaryId; children?: ReactNode; className?: string }) {
  const entry = GLOSSARY[id];
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const contentId = useId();
  const titleId = useId();

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Anchor asChild>
        <button
          ref={buttonRef}
          type="button"
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls={open ? contentId : undefined}
          onClick={() => setOpen((value) => !value)}
          className={cn(
            "inline cursor-help rounded-sm font-medium text-fg underline decoration-line-strong decoration-dotted decoration-2 underline-offset-4",
            "hover:decoration-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus",
            className,
          )}
        >
          {children ?? entry.term}
          <span className="sr-only">, show what this means</span>
        </button>
      </Popover.Anchor>
      <Popover.Portal>
        <Popover.Content
          id={contentId}
          aria-labelledby={titleId}
          side="top"
          align="start"
          sideOffset={8}
          collisionPadding={16}
          onInteractOutside={(event) => {
            if (buttonRef.current && event.target instanceof Node && buttonRef.current.contains(event.target)) {
              event.preventDefault();
            }
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            buttonRef.current?.focus();
          }}
          className="z-50 w-[min(22rem,calc(100vw-2rem))] rounded-xl border border-line bg-surface p-4 text-sm leading-6 text-fg shadow-overlay motion-safe:animate-pop-in"
        >
          <p id={titleId} className="font-semibold">
            {entry.term} <span className="font-normal text-muted">({entry.short})</span>
          </p>
          <p className="mt-1.5">{entry.definition}</p>
          <p className="mt-2 text-xs text-muted">Computed in {entry.source}.</p>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
