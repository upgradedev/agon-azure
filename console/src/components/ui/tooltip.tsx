/*
  Tooltip on Radix, for short hints on icon buttons. Tooltips do not open on
  tap, so nothing a phone visitor needs lives only in a tooltip: every icon
  button also carries an aria-label, and glossary terms use a popover
  (glossary-term.tsx) that opens on tap, click and Enter.
*/
import * as RadixTooltip from "@radix-ui/react-tooltip";
import type { ReactElement, ReactNode } from "react";

export const TooltipProvider = RadixTooltip.Provider;

export function Tip({ content, children }: { content: ReactNode; children: ReactElement }) {
  return (
    <RadixTooltip.Root>
      <RadixTooltip.Trigger asChild>{children}</RadixTooltip.Trigger>
      <RadixTooltip.Portal>
        <RadixTooltip.Content
          sideOffset={6}
          className="z-50 max-w-xs rounded-md bg-fg px-2.5 py-1.5 text-xs leading-5 text-bg shadow-raised motion-safe:animate-fade-in"
        >
          {content}
        </RadixTooltip.Content>
      </RadixTooltip.Portal>
    </RadixTooltip.Root>
  );
}
