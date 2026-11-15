"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type SyncStatus = "never_run" | "running" | "success" | "partial" | "failed" | "no_accounts";

export type AccountSyncResult = {
  account_index: number;
  email: string | null;
  success: boolean;
  /** Files written or updated in the local index during this run. */
  file_count: number;
  created_count: number;
  updated_count: number;
  deleted_count: number;
  error: string | null;
};

export type SyncSettlement = {
  run_id: string | null;
  status: SyncStatus;
  started_at: string | null;
  finished_at: string | null;
  account_count: number;
  succeeded_count: number;
  failed_count: number;
  total_files: number;
  total_deleted: number;
  error: string | null;
  accounts: AccountSyncResult[];
};

const POLL_INTERVAL_MS = 500;
const POLL_TIMEOUT_MS = 120_000;

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function requestStatus(): Promise<SyncSettlement | null> {
  try {
    const res = await fetch("/api/files/sync/status", { credentials: "include" });
    if (!res.ok) return null;
    return (await res.json()) as SyncSettlement;
  } catch {
    return null;
  }
}

/**
 * Triggers a sync and follows it to completion by polling the read-only
 * settlement endpoint.  The returned settlement always reflects the backend's
 * per-account outcome — a transport failure is surfaced as `error`, never as
 * a successful sync.
 */
export function useSyncSettlement() {
  const [settlement, setSettlement] = useState<SyncSettlement | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const runToken = useRef(0);
  const activeRef = useRef(true);

  useEffect(() => {
    activeRef.current = true;
    return () => {
      activeRef.current = false;
      runToken.current += 1;
    };
  }, []);

  /**
   * Load whatever the backend last settled.  A `running` record is ignored
   * here — it may be stale (e.g. the server restarted mid-sync), and showing
   * "Syncing…" forever would misreport reality.  Live runs are tracked by
   * `startSync` polling instead.
   */
  const loadLatest = useCallback(async () => {
    const latest = await requestStatus();
    if (!activeRef.current || !latest) return null;
    if (latest.status !== "never_run" && latest.status !== "running") setSettlement(latest);
    return latest;
  }, []);

  const startSync = useCallback(async (): Promise<SyncSettlement | null> => {
    const token = ++runToken.current;
    setSyncing(true);
    setError(null);

    try {
      const res = await fetch("/api/files/sync", { method: "POST", credentials: "include" });
      if (!res.ok) throw new Error(`Sync request failed (HTTP ${res.status})`);
      // The backend registers this run as "running" before replying, so the
      // first poll below already reflects the run we just started.
      await res.json().catch(() => ({}));
      const deadline = Date.now() + POLL_TIMEOUT_MS;

      while (activeRef.current && token === runToken.current) {
        const status = await requestStatus();
        if (!activeRef.current || token !== runToken.current) return null;

        if (status) {
          setSettlement(status);
          // `running` is the only state worth waiting on; anything else means
          // this run has settled (or a newer run already replaced it).
          if (status.status !== "running") return status;
        }

        if (Date.now() >= deadline) {
          setError("Timed out waiting for the sync to finish — it may still be running. Check again in a moment.");
          return null;
        }
        await sleep(POLL_INTERVAL_MS);
      }
      return null;
    } catch (e) {
      if (activeRef.current && token === runToken.current) {
        setError(e instanceof Error ? e.message : "Sync request failed");
      }
      return null;
    } finally {
      if (activeRef.current && token === runToken.current) setSyncing(false);
    }
  }, []);

  const dismiss = useCallback(() => {
    setSettlement(null);
    setError(null);
  }, []);

  return { settlement, syncing, error, loadLatest, startSync, dismiss };
}

/** Human-readable overall summary; never presents failures as success. */
export function summarizeSettlement(s: SyncSettlement): string {
  switch (s.status) {
    case "running":
      return s.account_count > 0 ? `Syncing ${s.account_count} account${s.account_count === 1 ? "" : "s"}…` : "Syncing…";
    case "no_accounts":
      return "No connected accounts to sync.";
    case "success":
      return `Sync complete — ${s.succeeded_count} of ${s.account_count} account${s.account_count === 1 ? "" : "s"} succeeded.`;
    case "partial":
      return `Sync completed with problems — ${s.succeeded_count} account${s.succeeded_count === 1 ? "" : "s"} succeeded, ${s.failed_count} failed.`;
    case "failed":
      return s.accounts.length > 0
        ? `Sync failed — all ${s.failed_count} account${s.failed_count === 1 ? "" : "s"} failed.`
        : `Sync failed${s.error ? ` — ${s.error}` : "."}`;
    default:
      return "No sync has run yet.";
  }
}
