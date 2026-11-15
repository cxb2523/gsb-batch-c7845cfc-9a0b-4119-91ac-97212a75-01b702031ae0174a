"use client";

import type { AccountSyncResult, SyncSettlement } from "@/hooks/useSyncSettlement";
import { summarizeSettlement } from "@/hooks/useSyncSettlement";

function accountLabel(result: AccountSyncResult): string {
  if (result.email) return result.email;
  return `Account #${result.account_index}`;
}

function toneForSettlement(status: SyncSettlement["status"]) {
  switch (status) {
    case "success":
      return {
        border: "border-emerald-500/30",
        bg: "bg-emerald-500/5",
        text: "text-emerald-400",
        label: "Sync complete",
      };
    case "partial":
      return {
        border: "border-amber-500/30",
        bg: "bg-amber-500/5",
        text: "text-amber-400",
        label: "Sync partially failed",
      };
    case "failed":
      return {
        border: "border-red-500/30",
        bg: "bg-red-500/5",
        text: "text-red-400",
        label: "Sync failed",
      };
    case "running":
      return {
        border: "border-dp-border",
        bg: "bg-dp-bg",
        text: "text-dp-text2",
        label: "Syncing",
      };
    case "no_accounts":
      return {
        border: "border-dp-border",
        bg: "bg-dp-bg",
        text: "text-dp-text2",
        label: "Nothing to sync",
      };
    default:
      return {
        border: "border-dp-border",
        bg: "bg-dp-bg",
        text: "text-dp-text2",
        label: "Sync",
      };
  }
}

function StatusIcon({ status }: { status: "success" | "failed" | "running" | "idle" }) {
  if (status === "running") {
    return (
      <div className="h-4 w-4 flex-shrink-0 animate-spin rounded-full border-2 border-dp-border border-t-orange-500" />
    );
  }
  if (status === "success") {
    return (
      <div className="flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full bg-emerald-500/20">
        <svg className="h-2.5 w-2.5 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
          <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
        </svg>
      </div>
    );
  }
  if (status === "failed") {
    return (
      <div className="flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full bg-red-500/20">
        <svg className="h-2.5 w-2.5 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
        </svg>
      </div>
    );
  }
  return <div className="h-4 w-4 flex-shrink-0 rounded-full border border-dp-border" />;
}

export function SyncResultPanel({
  settlement,
  error,
  onDismiss,
}: {
  settlement: SyncSettlement | null;
  error: string | null;
  onDismiss: () => void;
}) {
  if (error) {
    return (
      <div className="flex items-start gap-3 rounded-xl border border-red-500/30 bg-red-500/5 px-4 py-3">
        <StatusIcon status="failed" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-red-400">Sync could not be completed</p>
          <p className="mt-0.5 break-words text-xs text-dp-text2">{error}</p>
        </div>
        <button
          onClick={onDismiss}
          className="flex-shrink-0 rounded-lg p-1 text-dp-text3 transition hover:bg-dp-hover hover:text-dp-text"
          title="Dismiss"
        >
          <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    );
  }

  if (!settlement) return null;

  const tone = toneForSettlement(settlement.status);
  const isRunning = settlement.status === "running";
  const results = settlement.accounts;

  return (
    <div className={`rounded-xl border ${tone.border} ${tone.bg} px-4 py-3`}>
      <div className="flex items-start gap-3">
        <StatusIcon
          status={
            isRunning
              ? "running"
              : settlement.status === "failed" || settlement.status === "partial"
              ? "failed"
              : settlement.status === "success"
              ? "success"
              : "idle"
          }
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <p className={`text-sm font-medium ${tone.text}`}>{tone.label}</p>
            {!isRunning && settlement.status !== "no_accounts" && (
              <p className="text-xs text-dp-text2">
                {settlement.succeeded_count} succeeded · {settlement.failed_count} failed
                {settlement.total_files > 0 ? ` · ${settlement.total_files} file${settlement.total_files === 1 ? "" : "s"} indexed` : ""}
              </p>
            )}
          </div>
          <p className="mt-0.5 text-xs text-dp-text2">{summarizeSettlement(settlement)}</p>

          {results.length > 0 && (
            <ul className="mt-2.5 space-y-1.5">
              {results.map((result) => (
                <li
                  key={result.account_index}
                  className="flex items-start gap-2 rounded-lg border border-dp-border bg-dp-s1 px-2.5 py-1.5"
                >
                  <StatusIcon status={result.success ? "success" : "failed"} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-x-2">
                      <span className="text-xs font-medium text-dp-text">{accountLabel(result)}</span>
                      <span className="text-[10px] text-dp-text3">#{result.account_index}</span>
                      {result.success ? (
                        <span className="text-[11px] text-emerald-400">
                          {result.file_count} file{result.file_count === 1 ? "" : "s"}
                        </span>
                      ) : (
                        <span className="text-[11px] text-red-400">Failed</span>
                      )}
                    </div>
                    {result.success ? (
                      <p className="mt-0.5 text-[10px] text-dp-text3">
                        {result.created_count} new · {result.updated_count} updated · {result.deleted_count} removed
                      </p>
                    ) : (
                      <p className="mt-0.5 break-words text-[10px] text-red-300/90">{result.error ?? "Unknown error"}</p>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
        <button
          onClick={onDismiss}
          className="flex-shrink-0 rounded-lg p-1 text-dp-text3 transition hover:bg-dp-hover hover:text-dp-text"
          title="Dismiss"
        >
          <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </div>
  );
}
