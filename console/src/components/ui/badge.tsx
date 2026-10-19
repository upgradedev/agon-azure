import type { ComponentProps } from "react";
import { cn } from "../../lib/cn";

export type BadgeTone = "neutral" | "success" | "warning" | "danger" | "info" | "preview" | "solid";

const TONES: Record<BadgeTone, string> = {
  neutral: "border border-line bg-surface-2 text-fg",
  success: "bg-accent-bg text-accent",
  warning: "bg-notice-bg text-notice",
  danger: "bg-danger-bg text-danger",
  info: "bg-info-bg text-info",
  preview: "bg-preview-bg text-preview",
  solid: "bg-primary text-on-primary",
};

type BadgeProps = ComponentProps<"span"> & { tone?: BadgeTone };

export function Badge({ tone = "neutral", className, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs leading-5 font-medium whitespace-nowrap [&_svg]:size-3.5",
        TONES[tone],
        className,
      )}
      {...props}
    />
  );
}
