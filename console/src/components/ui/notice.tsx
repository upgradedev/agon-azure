/*
  A callout that says why in a full sentence. Every empty, loading, error and
  disabled state uses one, never a bare spinner.
*/
import type { ComponentProps, ReactNode } from "react";
import { TriangleAlert, CircleCheck, Info, Loader, Sparkles } from "lucide-react";
import { cn } from "../../lib/cn";

export type NoticeTone = "info" | "warning" | "danger" | "success" | "preview" | "loading";

const TONES: Record<NoticeTone, string> = {
  info: "border-info/25 bg-info-bg text-info",
  warning: "border-notice/25 bg-notice-bg text-notice",
  danger: "border-danger/25 bg-danger-bg text-danger",
  success: "border-accent/25 bg-accent-bg text-accent",
  preview: "border-preview/25 bg-preview-bg text-preview",
  loading: "border-line bg-surface-2 text-muted",
};

function NoticeIcon({ tone }: { tone: NoticeTone }) {
  const cls = "mt-0.5 size-4 shrink-0";
  switch (tone) {
    case "warning":
    case "danger":
      return <TriangleAlert className={cls} aria-hidden="true" />;
    case "success":
      return <CircleCheck className={cls} aria-hidden="true" />;
    case "preview":
      return <Sparkles className={cls} aria-hidden="true" />;
    case "loading":
      return <Loader className={cn(cls, "motion-safe:animate-spin")} aria-hidden="true" />;
    default:
      return <Info className={cls} aria-hidden="true" />;
  }
}

type NoticeProps = Omit<ComponentProps<"div">, "children"> & { tone?: NoticeTone; children: ReactNode };

export function Notice({ tone = "info", className, children, ...props }: NoticeProps) {
  return (
    <div className={cn("flex gap-2.5 rounded-lg border px-3.5 py-3 text-sm leading-6", TONES[tone], className)} {...props}>
      <NoticeIcon tone={tone} />
      <div className="min-w-0">{children}</div>
    </div>
  );
}
