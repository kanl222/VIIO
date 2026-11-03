import { useState, type ReactNode } from 'react';

/** Mount large trace data only while its native disclosure is open. */
export function LazyDetails({ summary, children, className, id }: {
  summary: ReactNode; children: () => ReactNode; className?: string; id?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  return <details className={className} id={id} onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary>{summary}</summary>
    {expanded ? children() : null}
  </details>;
}
