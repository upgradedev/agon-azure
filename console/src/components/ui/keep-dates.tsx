/*
  Text with each ISO date (2026-10-27) kept on one line. Browsers may break a
  line after a hyphen, which at 375 px split the status line's date in two.
  The words read the same; only the line breaks change.
*/
const DATE = /([0-9]{4}-[0-9]{2}-[0-9]{2})/;

export function KeepDates({ text }: { text: string }) {
  return (
    <>
      {text.split(DATE).map((part, i) =>
        i % 2 === 1 ? (
          <span key={i} className="whitespace-nowrap">
            {part}
          </span>
        ) : (
          part
        ),
      )}
    </>
  );
}
