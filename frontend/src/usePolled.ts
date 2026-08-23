import { useEffect, useState } from "react";

// Load once, then re-fetch on an interval, ignoring a result that arrives after the
// component using it has unmounted. Shared by every view that polls a summary endpoint
// (the ledger's run list, the impact strip) rather than each re-implementing the same
// load/interval/cancelled-flag dance.
export function usePolled<T>(fetcher: () => Promise<T>, intervalMs: number): T | null {
  const [value, setValue] = useState<T | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await fetcher();
        if (!cancelled) setValue(data);
      } catch {
        // Leave the previous value in place; the next poll retries.
      }
    }
    load();
    const interval = setInterval(load, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [intervalMs]);

  return value;
}
