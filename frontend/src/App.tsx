import { useEffect, useMemo, useState } from "react";

type ReviewStatus = "PENDING" | "ACCEPTED" | "REJECTED";
type FieldOutcome = "PASS" | "FAIL" | "REVIEW";

type ApplicationListItem = {
  application_id: string;
  brand_name: string;
  class_type: string;
  status: ReviewStatus;
};

type ApplicationDetail = ApplicationListItem & {
  producer: string;
  country_of_origin: string;
  alcohol_content: string;
  net_contents: string;
  government_warning: string;
  image_url: string;
};

type FieldResult = {
  field: keyof typeof FIELD_LABELS;
  expected_value: string;
  extracted_value: string | null;
  outcome: FieldOutcome;
  reason: string;
};

type VerificationResult = {
  application_id: string;
  verdict: "PASS" | "NEEDS_REVIEW";
  fields: FieldResult[];
  latency_ms: number;
};

type BatchVerificationItem = {
  application_id: string;
  result: VerificationResult | null;
  error: { code: string; message: string } | null;
};

type BatchVerificationSummary = {
  passed: number;
  needs_review: number;
  total: number;
  errors: number;
};

type BatchVerificationResponse = {
  items: BatchVerificationItem[];
  summary: BatchVerificationSummary;
  latency_ms: number;
  concurrency_limit: number;
};

type ApiErrorBody = { error?: { message?: string } };

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

const FIELD_LABELS = {
  brand_name: "Brand name",
  class_type: "Type of alcohol",
  producer: "Producer",
  country_of_origin: "Country of origin",
  alcohol_content: "Alcohol content",
  net_contents: "Container size",
  government_warning: "Government warning",
} as const;

const STATUS_LABELS: Record<ReviewStatus, string> = {
  PENDING: "Not reviewed",
  ACCEPTED: "Accepted",
  REJECTED: "Rejected",
};

async function requestJson<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: options?.body
        ? { "Content-Type": "application/json", ...options.headers }
        : options?.headers,
    });
  } catch {
    throw new Error("We could not reach the service. Check your connection and try again.");
  }

  if (!response.ok) {
    let message = "We could not complete that request. Please try again.";
    try {
      const body = (await response.json()) as ApiErrorBody;
      if (body.error?.message) message = body.error.message;
    } catch {
      // The fallback above is intentionally plain English.
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

type Route =
  | { page: "dashboard" }
  | { page: "review"; applicationId: string }
  | {
      page: "results";
      applicationId: string;
      items: BatchVerificationItem[];
      summary: BatchVerificationSummary;
    };

function getRoute(): Route {
  const review = window.location.pathname.match(/^\/review\/([^/]+)\/?$/);
  if (review) return { page: "review", applicationId: decodeURIComponent(review[1]) };
  const results = window.location.pathname.match(/^\/results\/([^/]+)\/?$/);
  if (results) {
    const state = window.history.state as {
      batchResponse?: BatchVerificationResponse;
      verificationResult?: VerificationResult;
      verificationResults?: VerificationResult[];
    } | null;
    if (state?.batchResponse) {
      return {
        page: "results",
        applicationId: decodeURIComponent(results[1]),
        items: state.batchResponse.items,
        summary: state.batchResponse.summary,
      };
    }
    const savedResults = state?.verificationResults ?? (state?.verificationResult ? [state.verificationResult] : []);
    const items = savedResults.map((result) => ({ application_id: result.application_id, result, error: null }));
    return {
      page: "results",
      applicationId: decodeURIComponent(results[1]),
      items,
      summary: {
        passed: savedResults.filter((result) => result.verdict === "PASS").length,
        needs_review: savedResults.filter((result) => result.verdict !== "PASS").length,
        total: savedResults.length,
        errors: 0,
      },
    };
  }
  return { page: "dashboard" };
}

function goTo(path: string, state: object = {}): void {
  window.history.pushState(state, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function StatusBadge({ status }: { status: ReviewStatus }) {
  return <span className={`status-badge status-${status.toLowerCase()}`}>{STATUS_LABELS[status]}</span>;
}

function ErrorNotice({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="error-notice" role="alert">
      <strong>Something went wrong</strong>
      <p>{message}</p>
      {retry && <button onClick={retry}>Try again</button>}
    </div>
  );
}

function PageHeader() {
  return (
    <header className="site-header">
      <div className="page-width header-content">
        <div className="brand-mark" aria-hidden="true">TTB</div>
        <div>
          <strong>Label Review</strong>
          <span>Review aid</span>
        </div>
      </div>
    </header>
  );
}

function Dashboard() {
  const [applications, setApplications] = useState<ApplicationListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [batchVerifying, setBatchVerifying] = useState(false);
  const [showBatchProgress, setShowBatchProgress] = useState(false);
  const [batchError, setBatchError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const records = await requestJson<ApplicationListItem[]>("/applications");
      setApplications(records);
      const selectableIds = new Set(
        records
          .filter((record) => record.status === "PENDING")
          .map((record) => record.application_id),
      );
      setSelectedIds((current) => current.size > 0
        ? new Set([...current].filter((applicationId) => selectableIds.has(applicationId)))
        : selectableIds);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Applications could not be loaded.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const counts = useMemo(() => ({
    PENDING: applications.filter((item) => item.status === "PENDING").length,
    ACCEPTED: applications.filter((item) => item.status === "ACCEPTED").length,
    REJECTED: applications.filter((item) => item.status === "REJECTED").length,
  }), [applications]);

  const resetDemo = async () => {
    setResetting(true);
    setError(null);
    try {
      await requestJson<{ reset_count: number }>("/applications/reset-statuses", { method: "POST" });
      setConfirmReset(false);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The demo could not be reset.");
    } finally {
      setResetting(false);
    }
  };

  const verifyBatch = async () => {
    if (selectedIds.size === 0) {
      setBatchError("Select at least one application to verify.");
      return;
    }
    setBatchVerifying(true);
    setBatchError(null);
    const progressTimer = window.setTimeout(() => setShowBatchProgress(true), 400);
    try {
      const batchResponse = await requestJson<BatchVerificationResponse>("/verify/batch", {
        method: "POST",
        body: JSON.stringify({ application_ids: [...selectedIds] }),
      });
      goTo("/results/batch", { batchResponse });
    } catch (caught) {
      setBatchError(caught instanceof Error ? caught.message : "The selected labels could not be verified.");
    } finally {
      window.clearTimeout(progressTimer);
      setShowBatchProgress(false);
      setBatchVerifying(false);
    }
  };

  const toggleSelected = (applicationId: string) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(applicationId)) next.delete(applicationId); else next.add(applicationId);
      return next;
    });
  };

  const selectableApplications = applications.filter((application) => application.status === "PENDING");
  const allSelected = selectableApplications.length > 0 && selectableApplications.every((application) => selectedIds.has(application.application_id));

  return (
    <>
      <PageHeader />
      <main id="main-content" className="page-width page-main dashboard-screen">
        <div className="title-row">
          <div>
            <p className="eyebrow">Your applications</p>
            <h1>Choose a label to review</h1>
            <p className="intro">Open an application, check its label, then accept or reject it.</p>
          </div>
          <div className="dashboard-actions">
            <button className="secondary-button" onClick={() => setConfirmReset(true)}>Refresh demo</button>
          </div>
        </div>

        {confirmReset && (
          <section className="confirmation" role="alertdialog" aria-labelledby="reset-title">
            <div>
              <h2 id="reset-title">Reset every status?</h2>
              <p>All applications will return to “Not reviewed.”</p>
            </div>
            <div className="button-group">
              <button className="danger-button" disabled={resetting} onClick={() => void resetDemo()}>
                {resetting ? "Resetting…" : "Yes, reset demo"}
              </button>
              <button className="quiet-button" disabled={resetting} onClick={() => setConfirmReset(false)}>Cancel</button>
            </div>
          </section>
        )}

        <section className="summary-grid" aria-label="Application status summary">
          <div><span>Not reviewed</span><strong>{counts.PENDING}</strong></div>
          <div><span>Accepted</span><strong>{counts.ACCEPTED}</strong></div>
          <div><span>Rejected</span><strong>{counts.REJECTED}</strong></div>
        </section>

        {error && <ErrorNotice message={error} retry={() => void load()} />}
        {batchError && <ErrorNotice message={batchError} retry={() => void verifyBatch()} />}
        {batchVerifying && showBatchProgress && (
          <div className="batch-progress" role="status" aria-live="polite">
            <div><strong>Checking {selectedIds.size} labels…</strong><span>Completed labels will appear together when the batch is ready.</span></div>
            <div className="progress-track" aria-hidden="true"><span /></div>
          </div>
        )}

        {loading ? (
          <div className="loading-panel" role="status"><span className="spinner" />Loading applications…</div>
        ) : !error && applications.length === 0 ? (
          <div className="empty-panel"><h2>No applications found</h2><p>There are no applications ready to review.</p></div>
        ) : !error && (
          <div className="application-list" aria-label="Applications">
            <div className="application-row application-head">
              <label className="list-select-all">
                <input
                  type="checkbox"
                  checked={allSelected}
                  disabled={selectableApplications.length === 0}
                  onChange={() => setSelectedIds(allSelected ? new Set() : new Set(selectableApplications.map((application) => application.application_id)))}
                  aria-label="Select all applications"
                />
              </label>
              <span>Application</span><span>Brand and type</span><span>Status</span><span>Action</span>
            </div>
            {applications.map((application) => {
              const complete = application.status !== "PENDING";
              return (
                <article className={`application-row ${complete ? `application-row-complete application-row-${application.status.toLowerCase()}` : ""}`} key={application.application_id}>
                  {complete ? (
                    <span className="list-control-placeholder" aria-hidden="true" />
                  ) : (
                    <label className="list-row-select" aria-label={`Select application ${application.application_id}`}>
                      <input type="checkbox" checked={selectedIds.has(application.application_id)} onChange={() => toggleSelected(application.application_id)} />
                    </label>
                  )}
                  <div><span className="mobile-label">Application</span><strong>{application.application_id}</strong></div>
                  <div><span className="mobile-label">Brand and type</span><strong>{application.brand_name}</strong><small>{application.class_type}</small></div>
                  <div><span className="mobile-label">Status</span><StatusBadge status={application.status} /></div>
                  {complete ? (
                    <div className="completion-label"><span className="mobile-label">Action</span><strong>Complete</strong></div>
                  ) : (
                    <button className="open-button" onClick={() => goTo(`/review/${encodeURIComponent(application.application_id)}`)}>Open review</button>
                  )}
                </article>
              );
            })}
          </div>
        )}
        {!loading && !error && applications.length > 0 && (
          <div className="dashboard-batch-action">
            <button className="primary-button batch-verify-button" disabled={batchVerifying || selectedIds.size === 0} onClick={() => void verifyBatch()}>
              {batchVerifying ? "Verifying…" : `Verify selected (${selectedIds.size})`}
            </button>
          </div>
        )}
      </main>
      <Disclaimer />
    </>
  );
}

function ComparisonResult({ result }: { result: VerificationResult }) {
  const approved = result.verdict === "PASS";
  return (
    <section className="results" aria-labelledby="results-title">
      <div className="field-results-heading">
        <div>
          <p className="eyebrow">Application {result.application_id}</p>
          <h2 id="results-title">Field-by-field results</h2>
        </div>
        <span className={`verdict-chip ${approved ? "verdict-approved" : "verdict-review"}`}>
          {approved ? "APPROVED" : "NEEDS REVIEW"}
        </span>
      </div>
      <div className="result-list">
        {result.fields.map((field) => {
          const needsAttention = field.outcome !== "PASS";
          return (
            <article className={`result-card result-${field.outcome.toLowerCase()}`} key={field.field}>
              <div className="result-heading">
                <h4>{FIELD_LABELS[field.field] ?? field.field}</h4>
                <span>{field.outcome === "PASS" ? "PASS" : field.outcome === "FAIL" ? "FAIL" : "CHECK"}</span>
              </div>
              <p>{needsAttention ? "The label does not clearly match the application." : "The label matches the application."}</p>
              {needsAttention && (
                <dl className="comparison-values">
                  <div><dt>Application says</dt><dd title={field.expected_value}>{field.expected_value}</dd></div>
                  <div><dt>Label shows</dt><dd title={field.extracted_value ?? "Nothing could be read"}>{field.extracted_value ?? "Nothing could be read"}</dd></div>
                </dl>
              )}
            </article>
          );
        })}
      </div>
      <p className="latency-note">Completed in {(result.latency_ms / 1000).toFixed(1)} seconds.</p>
    </section>
  );
}

function ReviewPage({ applicationId }: { applicationId: string }) {
  const [application, setApplication] = useState<ApplicationDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [imageError, setImageError] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [verifyError, setVerifyError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      setApplication(await requestJson<ApplicationDetail>(`/applications/${encodeURIComponent(applicationId)}`));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The application could not be loaded.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, [applicationId]);
  const verify = async () => {
    setVerifying(true);
    setVerifyError(null);
    try {
      const verificationResult = await requestJson<VerificationResult>("/verify", {
        method: "POST",
        body: JSON.stringify({ application_id: applicationId }),
      });
      goTo(`/results/${encodeURIComponent(applicationId)}`, { verificationResults: [verificationResult] });
    } catch (caught) {
      setVerifyError(caught instanceof Error ? caught.message : "The label could not be checked.");
    } finally {
      setVerifying(false);
    }
  };

  return (
    <>
      <PageHeader />
      <main id="main-content" className="page-width page-main review-screen">
        <button className="back-button" onClick={() => goTo("/")} aria-label="Back to all applications">← All applications</button>
        {loading ? (
          <div className="loading-panel" role="status"><span className="spinner" />Loading label and application…</div>
        ) : error ? (
          <ErrorNotice message={error} retry={() => void load()} />
        ) : application && (
          <>
            <div className="review-title">
              <div>
                <p className="eyebrow">Application {application.application_id}</p>
                <h1>Review this label</h1>
              </div>
              <StatusBadge status={application.status} />
            </div>

            <div className="review-grid">
              <section className="label-panel" aria-labelledby="label-heading">
                <h2 id="label-heading">Label image</h2>
                {imageError ? (
                  <div className="image-error" role="alert">
                    <strong>The label image could not be displayed.</strong>
                    <button onClick={() => setImageError(false)}>Try image again</button>
                  </div>
                ) : (
                  <img src={`${API_BASE}${application.image_url}`} alt={`Label for ${application.brand_name}`} onError={() => setImageError(true)} />
                )}
              </section>

              <section className="data-panel" aria-labelledby="application-heading">
                <h2 id="application-heading">Application information</h2>
                <dl className="field-list">
                  {(Object.keys(FIELD_LABELS) as Array<keyof typeof FIELD_LABELS>).map((field) => (
                    <div key={field} className={field === "government_warning" ? "warning-field" : ""}>
                      <dt>{FIELD_LABELS[field]}</dt>
                      <dd>{application[field]}</dd>
                    </div>
                  ))}
                </dl>
              </section>
            </div>

            <section className="verify-panel compact-verify">
              <div><h2>Ready to check?</h2><p>Usually less than 5 seconds.</p></div>
              <button className="primary-button" disabled={verifying} onClick={() => void verify()}>
                {verifying ? "Checking label…" : "Verify label"}
              </button>
            </section>
            {verifying && <div className="checking" role="status"><span className="spinner" />Reading and comparing all seven fields…</div>}
            {verifyError && <ErrorNotice message={verifyError} retry={() => void verify()} />}
          </>
        )}
      </main>
      <Disclaimer />
    </>
  );
}

function ResultsPage({
  applicationId,
  items,
  summary,
}: {
  applicationId: string;
  items: BatchVerificationItem[];
  summary: BatchVerificationSummary;
}) {
  const [saving, setSaving] = useState(false);
  const [decisionError, setDecisionError] = useState<string | null>(null);
  const [activeId, setActiveId] = useState(items[0]?.application_id ?? applicationId);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(
    () => new Set(items.map((item) => item.application_id)),
  );
  const [decisions, setDecisions] = useState<Record<string, ReviewStatus>>({});

  const saveDecisions = async (decision: "ACCEPTED" | "REJECTED") => {
    const applicationIds = [...selectedIds];
    if (applicationIds.length === 0) {
      setDecisionError("Select at least one application first.");
      return;
    }
    setSaving(true);
    setDecisionError(null);
    const outcomes = await Promise.allSettled(applicationIds.map((id) =>
      requestJson<ApplicationListItem>(`/applications/${encodeURIComponent(id)}/decision`, {
        method: "POST",
        body: JSON.stringify({ decision }),
      }),
    ));
    const savedIds = applicationIds.filter((_, index) => outcomes[index].status === "fulfilled");
    const failedCount = applicationIds.length - savedIds.length;
    setDecisions((current) => ({
      ...current,
      ...Object.fromEntries(savedIds.map((id) => [id, decision])),
    }));
    setSelectedIds((current) => new Set([...current].filter((id) => !savedIds.includes(id))));
    if (failedCount > 0) {
      setDecisionError(`${failedCount} decision${failedCount === 1 ? "" : "s"} could not be saved. Please try again.`);
    }
    setSaving(false);
  };

  const toggleSelected = (id: string) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const undecidedItems = items.filter((item) => (decisions[item.application_id] ?? "PENDING") === "PENDING");
  const allSelected = undecidedItems.length > 0 && undecidedItems.every((item) => selectedIds.has(item.application_id));
  const activeItem = items.find((item) => item.application_id === activeId) ?? items[0];

  return (
    <>
      <PageHeader />
      <main id="main-content" className="page-width page-main results-screen">
        <div className="results-topline">
          <div>
            <p className="eyebrow">Review completed checks</p>
            <h1>Verification results</h1>
          </div>
          <button className="finish-review-button" onClick={() => goTo("/")}>Finish review</button>
        </div>
        {items.length === 0 ? (
          <ErrorNotice
            message="These results are no longer available. Check the label again to create new results."
            retry={() => goTo(`/review/${encodeURIComponent(applicationId)}`)}
          />
        ) : (
          <>
            <section className="verification-summary" aria-label="Batch verification summary">
              <div className="summary-passed"><span>Passed</span><strong>{summary.passed}</strong></div>
              <div className="summary-review"><span>Needs review</span><strong>{summary.needs_review}</strong></div>
              <div><span>Total</span><strong>{summary.total}</strong></div>
              {summary.errors > 0 && <p>{summary.errors} label{summary.errors === 1 ? "" : "s"} could not be processed and are included in Needs review.</p>}
            </section>
            <section className="batch-toolbar" aria-label="Bulk decisions">
              <label>
                <input
                  type="checkbox"
                  checked={allSelected}
                  disabled={undecidedItems.length === 0}
                  onChange={() => setSelectedIds(allSelected ? new Set() : new Set(undecidedItems.map((item) => item.application_id)))}
                />
                Select remaining ({undecidedItems.length})
              </label>
              <span>{selectedIds.size} selected</span>
              <div className="batch-actions">
                <button className="accept-button" disabled={saving || selectedIds.size === 0} onClick={() => void saveDecisions("ACCEPTED")}>Accept selected</button>
                <button className="reject-button" disabled={saving || selectedIds.size === 0} onClick={() => void saveDecisions("REJECTED")}>Reject selected</button>
              </div>
            </section>
            <div className="batch-workspace">
              <aside className="results-queue" aria-label="Applications with results">
                {items.map((item) => {
                  const status = decisions[item.application_id] ?? "PENDING";
                  const passed = item.result?.verdict === "PASS";
                  const decided = status !== "PENDING";
                  return (
                    <article className={`queue-item ${activeItem?.application_id === item.application_id ? "queue-item-active" : ""} ${decided ? `queue-item-decided queue-item-${status.toLowerCase()}` : ""}`} key={item.application_id}>
                      <label className="queue-select" aria-label={`Select application ${item.application_id}`}>
                        <input type="checkbox" disabled={decided} checked={selectedIds.has(item.application_id)} onChange={() => toggleSelected(item.application_id)} />
                      </label>
                      <button className="queue-open" onClick={() => setActiveId(item.application_id)} aria-label={`View results for ${item.application_id}; ${STATUS_LABELS[status]}`}>
                        <strong>{item.application_id}</strong>
                        <span className={`verdict-chip ${passed ? "verdict-approved" : "verdict-review"}`}>
                          {passed ? "APPROVED" : "NEEDS REVIEW"}
                        </span>
                        <StatusBadge status={status} />
                      </button>
                    </article>
                  );
                })}
              </aside>
              {activeItem?.result ? (
                <ComparisonResult result={activeItem.result} />
              ) : activeItem?.error ? (
                <section className="results item-error-detail" aria-labelledby="item-error-title">
                  <div className="field-results-heading">
                    <div><p className="eyebrow">Application {activeItem.application_id}</p><h2 id="item-error-title">Label needs review</h2></div>
                    <span className="verdict-chip verdict-review">NEEDS REVIEW</span>
                  </div>
                  <div className="error-notice" role="alert"><strong>This label could not be processed</strong><p>{activeItem.error.message}</p></div>
                  <p>Select another application to view its field-by-field results, or retry this application from the dashboard.</p>
                </section>
              ) : null}
            </div>
            <div className="batch-footer">
              {decisionError && <ErrorNotice message={decisionError} />}
              <span role="status">{saving ? "Saving decisions…" : "Decisions are saved as you make them."}</span>
              <button className="secondary-button" onClick={() => goTo("/")}>Return to applications</button>
            </div>
          </>
        )}
      </main>
    </>
  );
}

function Disclaimer() {
  return <footer className="site-footer"><div className="page-width">This tool supports human review. It does not make a final legal or regulatory decision.</div></footer>;
}

export function App() {
  const [route, setRoute] = useState<Route>(getRoute);
  useEffect(() => {
    const updateRoute = () => setRoute(getRoute());
    window.addEventListener("popstate", updateRoute);
    return () => window.removeEventListener("popstate", updateRoute);
  }, []);

  return (
    <>
      <a className="skip-link" href="#main-content">Skip to main content</a>
      {route.page === "review" ? (
        <ReviewPage applicationId={route.applicationId} />
      ) : route.page === "results" ? (
        <ResultsPage applicationId={route.applicationId} items={route.items} summary={route.summary} />
      ) : (
        <Dashboard />
      )}
    </>
  );
}
