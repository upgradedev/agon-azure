/*
  A segmented control built from native radio inputs, so arrow keys, form
  semantics and screen readers work without script, and the prerendered page
  needs no inline style (a Radix radio group renders a styled hidden input).
*/
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export interface SegmentOption<T extends string> {
  value: T;
  label: ReactNode;
}

interface SegmentedProps<T extends string> {
  name: string;
  legend: ReactNode;
  value: T | null;
  options: SegmentOption<T>[];
  onChange: (value: T) => void;
  describedBy?: string;
  legendHidden?: boolean;
  className?: string;
}

export function Segmented<T extends string>({
  name,
  legend,
  value,
  options,
  onChange,
  describedBy,
  legendHidden = false,
  className,
}: SegmentedProps<T>) {
  return (
    <fieldset className={cn("min-w-0", className)} aria-describedby={describedBy}>
      <legend className={legendHidden ? "sr-only" : "mb-1.5 text-sm font-medium text-fg"}>{legend}</legend>
      <div className="inline-flex max-w-full flex-wrap gap-1 rounded-lg border border-line bg-surface-2 p-1">
        {options.map((option) => (
          <label key={option.value} className="relative cursor-pointer">
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              onChange={() => onChange(option.value)}
              className="peer sr-only"
            />
            <span
              className={cn(
                "block rounded-md px-3 py-1.5 text-sm font-medium text-muted transition-colors",
                "hover:text-fg peer-checked:bg-surface peer-checked:text-fg peer-checked:shadow-card",
                "peer-focus-visible:outline-2 peer-focus-visible:outline-offset-1 peer-focus-visible:outline-focus",
              )}
            >
              {option.label}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
