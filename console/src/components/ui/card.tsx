import type { ComponentProps } from "react";
import { cn } from "../../lib/cn";

export function Card({ className, ...props }: ComponentProps<"section">) {
  return <section className={cn("rounded-xl border border-line bg-surface shadow-card", className)} {...props} />;
}

export function CardHeader({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("flex items-start justify-between gap-3 px-5 pt-5", className)} {...props} />;
}

export function CardTitle({ className, ...props }: ComponentProps<"h2">) {
  return <h2 className={cn("text-[0.9375rem] font-semibold tracking-tight text-fg", className)} {...props} />;
}

export function CardBody({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("px-5 pt-3 pb-5", className)} {...props} />;
}
