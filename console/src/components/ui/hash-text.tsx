/*
  A full sha256 with a copy button. The status line is a polite live region;
  when the clipboard is refused, the hash is selected for the visitor instead.
*/
import { useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import { Button } from "./button";

export function HashText({ hash, label, id }: { hash: string; label: string; id: string }) {
  const codeRef = useRef<HTMLElement>(null);
  const [status, setStatus] = useState("");
  const [copied, setCopied] = useState(false);

  function selectHash(): void {
    const node = codeRef.current;
    const selection = window.getSelection();
    if (!node || !selection) {
      return;
    }
    const range = document.createRange();
    range.selectNodeContents(node);
    selection.removeAllRanges();
    selection.addRange(range);
  }

  async function onCopy(): Promise<void> {
    try {
      if (!navigator.clipboard) {
        throw new Error("no clipboard");
      }
      await navigator.clipboard.writeText(hash);
      setCopied(true);
      setStatus(`Copied the full ${label.toLowerCase()}.`);
    } catch {
      selectHash();
      setStatus("This browser did not allow copying, so the full hash is selected for you to copy.");
    }
  }

  return (
    <div className="min-w-0">
      <div className="flex items-start gap-2">
        <code
          ref={codeRef}
          id={id}
          className="min-w-0 flex-1 rounded-md border border-line bg-surface-2 px-2.5 py-1.5 font-mono text-xs leading-5 break-all text-fg"
        >
          {hash}
        </code>
        <Button variant="secondary" size="sm" onClick={onCopy} aria-describedby={id}>
          {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <p className="mt-1 min-h-5 text-xs text-muted" aria-live="polite">
        {status}
      </p>
    </div>
  );
}
