/*
  Dialog and Sheet on Radix Dialog, chosen for focus trap, Escape, modal
  semantics and focus return.

  CSP note: Radix's Dialog.Overlay wraps react-remove-scroll, which injects a
  <style> element at run time; style-src 'self' blocks that. So this wrapper
  never renders Dialog.Overlay. It draws its own backdrop (a plain element
  with classes) and locks page scroll by toggling a class on <html>, so
  nothing writes inline CSS text. Playwright fails on any CSP violation while
  these open (e2e/overlays.spec.ts).

  There is no Dialog.Trigger either: Radix gives the trigger aria-controls
  pointing at content that is not in the prerendered page, which would be a
  broken reference. Callers open the dialog from their own button and pass it
  as returnFocusRef, so focus goes back there on close.
*/
import * as RadixDialog from "@radix-ui/react-dialog";
import { useEffect, type ReactNode, type RefObject } from "react";
import { X } from "lucide-react";
import { cn } from "../../lib/cn";

type Placement = "center" | "sheet";

interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description: ReactNode;
  children?: ReactNode;
  returnFocusRef?: RefObject<HTMLElement | null>;
  placement?: Placement;
  eyebrow?: ReactNode;
  contentId?: string;
  footer?: ReactNode;
  closeLabel?: string;
}

function useScrollLock(open: boolean): void {
  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const root = document.documentElement;
    root.classList.add("scroll-locked");
    return () => root.classList.remove("scroll-locked");
  }, [open]);
}

const PLACEMENT: Record<Placement, string> = {
  center:
    "fixed top-1/2 left-1/2 z-50 flex max-h-[min(40rem,calc(100dvh-2rem))] w-[calc(100vw-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 " +
    "flex-col rounded-2xl border border-line bg-surface shadow-overlay motion-safe:animate-pop-in",
  sheet:
    "fixed inset-x-0 bottom-0 z-50 flex max-h-[88dvh] flex-col rounded-t-2xl border border-line bg-surface shadow-overlay motion-safe:animate-slide-up " +
    "sm:inset-y-0 sm:right-0 sm:left-auto sm:max-h-none sm:w-[30rem] sm:rounded-none sm:rounded-l-2xl sm:motion-safe:animate-slide-left",
};

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  returnFocusRef,
  placement = "center",
  eyebrow,
  contentId,
  footer,
  closeLabel = "Close",
}: DialogProps) {
  useScrollLock(open);
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <div className="fixed inset-0 z-50 bg-backdrop motion-safe:animate-fade-in" aria-hidden="true" />
        <RadixDialog.Content
          id={contentId}
          className={PLACEMENT[placement]}
          onCloseAutoFocus={(event) => {
            const target = returnFocusRef?.current;
            if (target) {
              event.preventDefault();
              target.focus();
            }
          }}
        >
          <div className="flex items-start gap-3 border-b border-line px-5 py-4">
            <div className="min-w-0 flex-1">
              {eyebrow ? <div className="mb-1">{eyebrow}</div> : null}
              <RadixDialog.Title className="text-base font-semibold tracking-tight text-fg">{title}</RadixDialog.Title>
              <RadixDialog.Description className="mt-1 text-sm leading-6 text-muted">{description}</RadixDialog.Description>
            </div>
            <RadixDialog.Close
              className={cn(
                "inline-flex size-9 shrink-0 items-center justify-center rounded-lg text-muted transition-colors hover:bg-surface-2 hover:text-fg",
                "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus",
              )}
              aria-label={closeLabel}
            >
              <X className="size-4" aria-hidden="true" />
            </RadixDialog.Close>
          </div>
          {children ? <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-4">{children}</div> : null}
          {footer ? <div className="flex flex-wrap justify-end gap-2 border-t border-line px-5 py-3">{footer}</div> : null}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
