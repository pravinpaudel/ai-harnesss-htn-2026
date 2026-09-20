import { FormEvent, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Database, Plug, RefreshCw } from "lucide-react";
import { getConfig, getIngestReport, submitIngest } from "../api/client";
import type { DatasetSummary, IngestReport, SourceType } from "../api/types";
import { relativeTime, shortHash } from "../format";

/** Load research material, then make it available in the workspace. */

interface Props {
  datasets: DatasetSummary[];
  activeDataset?: string;
  onUseDataset: (name: string) => void;
  onDatasetsChanged: () => void;
}

const POLL_MS = 1500;
const SLOW_AFTER_S = 25;

export default function ConnectView({ datasets, activeDataset, onUseDataset, onDatasetsChanged }: Props) {
  const [source, setSource] = useState<SourceType>("mcp");
  const [datasetName, setDatasetName] = useState("mcp-financial-data");
  const [mcpUrl, setMcpUrl] = useState("");
  const [path, setPath] = useState("");
  const [jobId, setJobId] = useState<string>();
  const [report, setReport] = useState<IngestReport>();
  const [error, setError] = useState<string>();
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const abort = new AbortController();
    void getConfig(abort.signal)
      .then((config) => {
        if (config.mcp_url) setMcpUrl((current) => current || config.mcp_url || "");
        if (config.dataset_name) setDatasetName((current) => current === "mcp-financial-data" ? config.dataset_name! : current);
      })
      .catch(() => undefined);
    return () => abort.abort();
  }, []);

  useEffect(() => {
    if (!jobId || report) return undefined;
    const abort = new AbortController();
    const started = Date.now();
    const tick = async () => {
      setElapsed(Math.round((Date.now() - started) / 1000));
      try {
        const next = await getIngestReport(jobId, abort.signal);
        if (next) {
          setReport(next);
          onDatasetsChanged();
        }
      } catch (caught) {
        if (!abort.signal.aborted) setError(caught instanceof Error ? caught.message : "The ingest job could not be read.");
      }
    };
    void tick();
    const timer = setInterval(() => void tick(), POLL_MS);
    return () => {
      abort.abort();
      clearInterval(timer);
    };
  }, [jobId, report, onDatasetsChanged]);

  async function start(event: FormEvent) {
    event.preventDefault();
    setError(undefined);
    setReport(undefined);
    setElapsed(0);
    try {
      setJobId(await submitIngest({
        source,
        dataset_name: datasetName.trim() || "corpus",
        ...(source === "mcp" && mcpUrl.trim() ? { mcp_url: mcpUrl.trim() } : {}),
        ...(source === "file" ? { path: path.trim() } : {}),
      }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The ingest could not be queued.");
    }
  }

  const pending = Boolean(jobId) && !report && !error;
  const failed = report?.status === "failed" || (report?.errors.length ?? 0) > 0;

  return (
    <div className="connect">
      <div className="connect-cols">
        <form className="panel connect-form" onSubmit={start}>
          <h2 className="panel-title">
            <Plug size={16} aria-hidden="true" /> Add a research source
          </h2>
          <p className="panel-note">
            Add a research source for this workspace. Every answer keeps a link back to the source material.
          </p>

          <fieldset className="field">
            <legend>Source</legend>
            <label className="radio">
              <input type="radio" name="source" value="mcp" checked={source === "mcp"} onChange={() => setSource("mcp")} />
              <span>
                Research data source <span className="field-hint">paste an MCP address below</span>
              </span>
            </label>
            <label className="radio">
              <input type="radio" name="source" value="file" checked={source === "file"} onChange={() => setSource("file")} />
              <span>
                Files <span className="field-hint">a path the API host can read</span>
              </span>
            </label>
          </fieldset>

          <label className="field">
            <span className="field-label">Name this source</span>
            <input value={datasetName} onChange={(event) => setDatasetName(event.target.value)} required />
          </label>

          {source === "mcp" && (
            <label className="field">
              <span className="field-label" id="mcp-url-label">Research data URL</span>
              <input
                aria-labelledby="mcp-url-label"
                type="url"
                value={mcpUrl}
                onChange={(event) => setMcpUrl(event.target.value)}
                placeholder="https://research.example.com/mcp"
              />
              <span className="field-hint">Use the address supplied by your data provider. Leave blank to use the firm default.</span>
            </label>
          )}

          {source === "file" && (
            <label className="field">
              <span className="field-label">Path on the API host</span>
              <input
                className="mono"
                value={path}
                onChange={(event) => setPath(event.target.value)}
                placeholder="/workspace/contracts/fixture/raw"
                required
              />
            </label>
          )}

          <button type="submit" className="primary-button" disabled={pending}>
            {pending ? "Adding source…" : "Add source"}
          </button>
          <p className="panel-note">
            This may take a few minutes for a large source.
          </p>
        </form>

        <div className="panel connect-status" aria-live="polite">
          <h2 className="panel-title">Source status</h2>

          {!jobId && !error && (
            <ol className="stage-list">
              {[
                ["Getting the source", "We retrieve the documents and preserve the originals."],
                ["Preparing citations", "We identify the passages that can support an answer."],
                ["Checking the material", "We check dates, figures and potentially conflicting claims."],
              ].map(([name, detail], index) => (
                <li key={name}>
                  <span className="stage-n">{index + 1}</span>
                  <span>
                    <strong>{name}</strong>
                    <span className="stage-detail">{detail}</span>
                  </span>
                </li>
              ))}
            </ol>
          )}

          {error && (
            <div className="notice notice-error">
              <h3 className="notice-title">We couldn’t add this source</h3>
              <p>{error}</p>
            </div>
          )}

          {pending && (
            <div className="pending">
              <p className="working-head">
                <span className="dots" aria-hidden="true">
                  <i />
                  <i />
                  <i />
                </span>
                Job queued · {elapsed}s
              </p>
              <p className="panel-note mono">{jobId}</p>
              {elapsed > SLOW_AFTER_S && (
                <p className="panel-note">
                  Still no report. A full MCP corpus takes a while, but if nothing is running the worker, this waits
                  forever — the API reports queued, running and failed jobs the same way.
                </p>
              )}
            </div>
          )}

          {report && (
            <div className="report">
              <div className="report-head">
                <span className={`status-badge tone-${failed ? "declined" : "ok"}`}>
                  {failed ? <AlertTriangle size={15} aria-hidden="true" /> : <CheckCircle2 size={15} aria-hidden="true" />}
                  {failed ? "Failed" : report.reused ? "Reused existing version" : "Ready"}
                </span>
                <span className="report-meta">
                  {report.source.toUpperCase()} · parser {report.parser_version}
                  {report.source_hash && (
                    <>
                      {" "}
                      · snapshot <span className="mono">{shortHash(report.source_hash)}</span>
                    </>
                  )}
                </span>
              </div>

              {Object.keys(report.timings_ms).length > 0 && (
                <ul className="chip-list">
                  {Object.entries(report.timings_ms).map(([stage, ms]) => (
                    <li key={stage} className="chip">
                      <span className="chip-label">{stage}</span>
                      <span className="chip-count">{ms.toLocaleString()} ms</span>
                    </li>
                  ))}
                </ul>
              )}

              {report.documents.length > 0 && (
                <table className="report-table">
                  <caption className="sr-only">Documents ingested</caption>
                  <thead>
                    <tr>
                      <th scope="col">Document</th>
                      <th scope="col">Lines</th>
                      <th scope="col">Chunks</th>
                      <th scope="col">Tables</th>
                      <th scope="col">Facts</th>
                      <th scope="col">Untyped</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.documents.map((document) => (
                      <tr key={document.document_id}>
                        <td className="mono">{document.name}</td>
                        <td>{document.lines.toLocaleString()}</td>
                        <td>{document.chunks.toLocaleString()}</td>
                        <td>{document.tables}</td>
                        <td>{document.facts.toLocaleString()}</td>
                        <td>{document.untyped_cells.toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              <p className="panel-note">
                {report.findings} validation findings
                {report.warnings.length > 0 && ` · ${report.warnings.length} warnings`}
              </p>
              {report.errors.length > 0 && (
                <div className="notice notice-error">
                  <h3 className="notice-title">Errors</h3>
                  <ul>
                    {report.errors.map((message) => (
                      <li key={message}>{message}</li>
                    ))}
                  </ul>
                </div>
              )}
              {report.mcp_capabilities && (
                <details className="fold">
                  <summary>MCP capabilities as discovered</summary>
                  <pre className="capabilities mono">{JSON.stringify(report.mcp_capabilities, null, 2)}</pre>
                </details>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2 className="panel-title">
            <Database size={16} aria-hidden="true" /> Available research sources
          </h2>
          <button type="button" className="ghost-button subtle" onClick={onDatasetsChanged}>
            <RefreshCw size={14} aria-hidden="true" /> Refresh
          </button>
        </div>
        {datasets.length === 0 ? (
          <p className="panel-note">No research sources are available yet.</p>
        ) : (
          <ul className="dataset-list">
            {datasets.map((dataset) => {
              const active = dataset.name === activeDataset;
              return (
                <li key={dataset.dataset_version_id} className={`dataset-row${active ? " is-active" : ""}`}>
                  <span className="dataset-row-name">{dataset.name}</span>
                  <span className="dataset-row-meta">
                    {dataset.source_type === "mcp" ? "Research data source" : "Uploaded files"}
                    {relativeTime(dataset.ready_at) && ` · updated ${relativeTime(dataset.ready_at)}`}
                  </span>
                  <button
                    type="button"
                    className="ghost-button subtle"
                    onClick={() => onUseDataset(dataset.name)}
                    disabled={active}
                  >
                    {active ? "In use" : "Use for research"}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
