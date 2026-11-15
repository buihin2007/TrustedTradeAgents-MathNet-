/** Shown while a page in the workspace loads: grey blocks in the shape of the content, no layout jump. */
export default function Loading() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading">
      <div className="h-7 w-48 animate-pulse rounded-lg bg-slate-200" />
      <div className="h-32 animate-pulse rounded-xl bg-slate-200" />
      <div className="h-20 animate-pulse rounded-xl bg-slate-200" />
    </div>
  );
}
