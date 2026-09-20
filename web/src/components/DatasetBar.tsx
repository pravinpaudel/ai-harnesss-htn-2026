import { Database } from "lucide-react";
import type { DatasetSummary } from "../api/types";
import { relativeTime } from "../format";

/** Which corpus answered, and how fresh it is. */

export default function DatasetBar({ dataset, online }: { dataset?: DatasetSummary; online: boolean | null }) {
  return (
    <div className="dataset-bar">
      <span className="dataset-chip">
        <Database size={14} aria-hidden="true" />
        {dataset ? (
          <>
            <span className="dataset-name mono">{dataset.name}</span>
            <span className="dataset-version">v{dataset.version_no}</span>
          </>
        ) : (
          <span className="dataset-name">No dataset loaded</span>
        )}
      </span>
      {dataset && (
        <span className="dataset-fact">
          {relativeTime(dataset.ready_at) ? `Updated ${relativeTime(dataset.ready_at)}` : "Ready to research"}
        </span>
      )}
      <span className={`health ${online === false ? "is-down" : online === null ? "is-unknown" : "is-up"}`}>
        <i aria-hidden="true" />
        {online === null ? "Checking service" : online ? "Research service online" : "Research service offline"}
      </span>
    </div>
  );
}
