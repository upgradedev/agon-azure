/*
  The frame around every screen: skip link, top bar with the judge path, the
  build label, the footer. Navigation is plain links to hash routes, so it
  works in the prerendered page before any script runs.
*/
import { useId, useRef, useState, type ReactNode } from "react";
import { Menu, Moon, Sun } from "lucide-react";
import { Dialog } from "./ui/dialog";
import { Tip } from "./ui/tooltip";
import { Badge } from "./ui/badge";
import { cn } from "../lib/cn";
import { REPO_URL } from "../lib/commit";
import { hrefFor, type ViewKey } from "../lib/router";
import { setGuideSkipped, setTheme, useCommit, useGuideSkipped, useTheme } from "../hooks/stores";

export const PATH_STEPS: { view: ViewKey; label: string; long: string }[] = [
  { view: "home", label: "Match Centre", long: "Pick a match and blow full time" },
  { view: "newsroom", label: "Newsroom", long: "Watch the Newsroom Wall" },
  { view: "approve", label: "Approve", long: "Approve on the stand-in card" },
  { view: "recap", label: "Recap", long: "Read the editions" },
  { view: "verify", label: "Verify", long: "Verify the hash" },
];

export const MORE_LINKS: { view: ViewKey; label: string }[] = [
  { view: "media-office", label: "Media office" },
  { view: "how-it-works", label: "How it works" },
  { view: "data-lab", label: "Data Lab" },
  { view: "about", label: "About" },
];

function BrandMark({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="8" className="fill-primary" />
      <path d="M9 24 16 8l7 16" className="fill-none stroke-on-primary" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="16" cy="19.5" r="2.2" className="fill-on-primary" />
    </svg>
  );
}

function NavLink({ view, active, children, className }: { view: ViewKey; active: boolean; children: ReactNode; className?: string }) {
  return (
    <a
      href={hrefFor(view)}
      aria-current={active ? "page" : undefined}
      className={cn(
        "inline-flex h-9 items-center gap-2 rounded-lg px-2.5 text-sm font-medium no-underline transition-colors",
        active ? "bg-surface-2 text-fg" : "text-muted hover:bg-surface-2 hover:text-fg",
        className,
      )}
    >
      {children}
    </a>
  );
}

function StepNumber({ n, active }: { n: number; active: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "inline-flex size-5 items-center justify-center rounded-full text-[0.6875rem] font-semibold tabular-nums",
        active ? "bg-primary text-on-primary" : "border border-line-strong text-muted",
      )}
    >
      {n}
    </span>
  );
}

function ThemeToggle() {
  const theme = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  const label = theme === null ? "Switch colour theme" : `Switch to ${next} theme`;
  return (
    <Tip content={label}>
      <button
        type="button"
        aria-label={label}
        onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        className="inline-flex size-9 items-center justify-center rounded-lg text-muted transition-colors hover:bg-surface-2 hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
      >
        <Moon className="size-4 dark:hidden" aria-hidden="true" />
        <Sun className="hidden size-4 dark:block" aria-hidden="true" />
      </button>
    </Tip>
  );
}

function MobileMenu({ view }: { view: ViewKey }) {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  /* After choosing a page, focus belongs to that page's heading, not to the menu button. */
  const navigating = useRef(false);
  const contentId = useId();
  const choose = () => {
    navigating.current = true;
    setOpen(false);
  };
  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        aria-label="Open the menu"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? contentId : undefined}
        onClick={() => {
          navigating.current = false;
          setOpen(true);
        }}
        className="inline-flex size-9 items-center justify-center rounded-lg text-fg transition-colors hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus xl:hidden"
      >
        <Menu className="size-5" aria-hidden="true" />
      </button>
      <Dialog
        open={open}
        onOpenChange={setOpen}
        placement="sheet"
        contentId={contentId}
        returnFocusRef={navigating.current ? undefined : buttonRef}
        title="Menu"
        description="The five steps of the judge path, then the pages that explain how Agon works."
      >
        <nav aria-label="Judge path, menu">
          <ol className="space-y-1">
            {PATH_STEPS.map((step, i) => (
              <li key={step.view}>
                <a
                  href={hrefFor(step.view)}
                  onClick={choose}
                  aria-current={view === step.view ? "page" : undefined}
                  className={cn(
                    "flex items-center gap-3 rounded-lg px-3 py-2.5 no-underline transition-colors",
                    view === step.view ? "bg-surface-2 text-fg" : "text-fg hover:bg-surface-2",
                  )}
                >
                  <StepNumber n={i + 1} active={view === step.view} />
                  <span className="flex flex-col">
                    <span className="text-sm font-medium">{step.label}</span>
                    <span className="text-xs text-muted">{step.long}</span>
                  </span>
                </a>
              </li>
            ))}
          </ol>
        </nav>
        <nav aria-label="More, menu" className="mt-4 border-t border-line pt-4">
          <ul className="space-y-1">
            {MORE_LINKS.map((link) => (
              <li key={link.view}>
                <a
                  href={hrefFor(link.view)}
                  onClick={choose}
                  aria-current={view === link.view ? "page" : undefined}
                  className={cn(
                    "block rounded-lg px-3 py-2.5 text-sm font-medium no-underline transition-colors",
                    view === link.view ? "bg-surface-2 text-fg" : "text-fg hover:bg-surface-2",
                  )}
                >
                  {link.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </Dialog>
    </>
  );
}

function BuildLabel() {
  const commit = useCommit();
  return (
    <p className="text-xs text-muted" id="run-label">
      {commit ? (
        <>
          Preview build, commit{" "}
          <a href={`${REPO_URL}/commit/${commit}`} aria-label={`commit ${commit}`} className="font-mono text-link">
            {commit.slice(0, 7)}
          </a>
        </>
      ) : (
        "Preview build, commit not stamped (local build)"
      )}
    </p>
  );
}

export function Shell({ view, children }: { view: ViewKey; children: ReactNode }) {
  const guideSkipped = useGuideSkipped();
  return (
    <div className="flex min-h-dvh flex-col">
      <a
        href="#main"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("main")?.focus();
        }}
        className="sr-only z-[60] rounded-lg bg-primary px-4 py-2 text-on-primary no-underline focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-40 border-b border-line bg-surface/85 backdrop-blur supports-[backdrop-filter]:bg-surface/70">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-3 px-4 sm:px-6">
          <a href={hrefFor("home")} className="flex items-center gap-2 text-fg no-underline" aria-label="Agon, go to the Match Centre">
            <BrandMark className="size-7" />
            <span className="text-[0.9375rem] font-semibold tracking-tight">Agon</span>
          </a>
          <Badge tone="preview" className="hidden sm:inline-flex">
            Preview
          </Badge>
          <nav aria-label="Judge path" className="ml-4 hidden lg:block">
            <ol className="flex items-center gap-0.5">
              {PATH_STEPS.map((step, i) => (
                <li key={step.view}>
                  <NavLink view={step.view} active={view === step.view}>
                    <StepNumber n={i + 1} active={view === step.view} />
                    {step.label}
                  </NavLink>
                </li>
              ))}
            </ol>
          </nav>
          <div className="ml-auto flex items-center gap-1">
            <nav aria-label="More" className="hidden xl:block">
              <ul className="flex items-center gap-0.5">
                {MORE_LINKS.map((link) => (
                  <li key={link.view}>
                    <NavLink view={link.view} active={view === link.view}>
                      {link.label}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </nav>
            <ThemeToggle />
            <MobileMenu view={view} />
          </div>
        </div>
      </header>
      <div className="border-b border-line bg-surface-2/60">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-4 gap-y-1 px-4 py-1.5 sm:px-6">
          <BuildLabel />
          <p className="text-xs text-muted">Synthetic, fictional clubs and players only.</p>
        </div>
      </div>
      <main id="main" tabIndex={-1} className="flex-1 focus:outline-none">
        {children}
      </main>
      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-7xl flex-col gap-2 px-4 py-6 text-xs text-muted sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <p>
            Agon preview build. Clubs, players and matches are synthetic and fictional; Agon is not affiliated with any league, club
            or broadcaster.
          </p>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            {guideSkipped ? (
              <button
                type="button"
                onClick={() => setGuideSkipped(false)}
                className="rounded-sm text-link underline underline-offset-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
              >
                Show the guide again
              </button>
            ) : null}
            <a href={REPO_URL}>Source on GitHub, MIT licence</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
