import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight, GitPullRequest, RefreshCw } from 'lucide-react';
import type { JobListItem } from '../api/client';
import { fetchJobs } from '../api/client';

/* ─── Helpers ──────────────────────────────────────────────────────────────── */
const getStatusDisplay = (status: string): { label: string; cls: string } => {
  switch (status) {
    case 'SUCCESS':             return { label: 'Validated',  cls: 'badge-success' };
    case 'PARTIALLY_VALIDATED': return { label: 'Partial',    cls: 'badge-warning' };
    case 'ESCALATED':           return { label: 'Escalated',  cls: 'badge-error' };
    case 'REVIEWING':           return { label: 'Reviewing',  cls: 'badge-running' };
    case 'REPAIRING':           return { label: 'Repairing',  cls: 'badge-running' };
    case 'SNAPSHOTTING':        return { label: 'Snapshotting',cls:'badge-running' };
    default:                    return { label: status.charAt(0) + status.slice(1).toLowerCase(), cls: 'badge-default' };
  }
};

const getRiskDisplay = (risk: string): { cls: string; dot: string } => {
  switch (risk) {
    case 'CRITICAL': return { cls: 'badge-error',   dot: 'dot-error' };
    case 'HIGH':     return { cls: 'badge-error',   dot: 'dot-error' };
    case 'MEDIUM':   return { cls: 'badge-warning', dot: 'dot-warning' };
    default:         return { cls: 'badge-success', dot: 'dot-success' };
  }
};

const formatDate = (iso: string | null) => {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
};

/* ─── Component ────────────────────────────────────────────────────────────── */
export const Dashboard: React.FC = () => {
  const [jobs, setJobs] = useState<JobListItem[]>([]);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try { setJobs(await fetchJobs()); }
    catch (e) { console.error(e); }
    finally { setLoading(false); }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  const active    = jobs.filter(j => ['REVIEWING','REPAIRING','RECEIVED','SNAPSHOTTING'].includes(j.status)).length;
  const resolved  = jobs.filter(j => j.status === 'SUCCESS').length;
  const escalated = jobs.filter(j => j.status === 'ESCALATED').length;

  return (
    <>
      {/* Header */}
      <div className="page-header flex items-center justify-between">
        <div>
          <h1 className="page-title">PR Jobs</h1>
          <p className="page-subtitle">Live feed of automated pull request analyses.</p>
        </div>
        <button onClick={load} className="btn btn-secondary btn-sm" disabled={loading}>
          <RefreshCw size={13} className={loading ? 'spin' : ''} />
          Refresh
        </button>
      </div>

      {/* Stats */}
      <div className="grid-stats">
        <div className="stat-card">
          <p className="stat-label">Active</p>
          <p className="stat-value">{active}</p>
          <p className="stat-meta">In-progress analyses</p>
        </div>
        <div className="stat-card">
          <p className="stat-label">Resolved</p>
          <p className="stat-value" style={{ color: resolved > 0 ? '#1a6f1a' : 'var(--ink)' }}>{resolved}</p>
          <p className="stat-meta">Auto-validated patches</p>
        </div>
        <div className="stat-card">
          <p className="stat-label">Escalated</p>
          <p className="stat-value" style={{ color: escalated > 0 ? 'var(--error-deep)' : 'var(--ink)' }}>{escalated}</p>
          <p className="stat-meta">Requires human review</p>
        </div>
        <div className="stat-card">
          <p className="stat-label">Total</p>
          <p className="stat-value">{jobs.length}</p>
          <p className="stat-meta">All-time PR jobs</p>
        </div>
      </div>

      {/* Table */}
      <div className="card">
        <div style={{ padding: 'var(--space-md) var(--space-lg)', borderBottom: '1px solid var(--hairline)' }}>
          <span className="section-eyebrow">Pull Request Jobs</span>
        </div>

        {jobs.length === 0 ? (
          <div className="empty-state" style={{ margin: 'var(--space-lg)' }}>
            <GitPullRequest size={32} />
            <span className="empty-state-title">No jobs recorded.</span>
            <span className="empty-state-body">
              Configure a GitHub webhook pointing to this server to start receiving pull request events.
            </span>
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Repository / PR</th>
                  <th>Status</th>
                  <th>Risk</th>
                  <th>Confidence</th>
                  <th>Created</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {jobs.map(job => {
                  const sm = getStatusDisplay(job.status);
                  const rm = getRiskDisplay(job.risk_level);
                  return (
                    <tr key={job.id} className="row-link">
                      <td>
                        <Link to={`/jobs/${job.id}`} style={{ display: 'block' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <span style={{ fontWeight: 500, color: 'var(--ink)', letterSpacing: '-0.28px' }}>
                              {job.repository}
                            </span>
                            <span className="font-mono text-xs text-muted">#{job.pr_number}</span>
                          </div>
                          <div className="truncate text-sm text-body mt-1" style={{ maxWidth: 360 }}>
                            {job.pr_title || 'Untitled'}
                          </div>
                        </Link>
                      </td>
                      <td><span className={`badge ${sm.cls}`}>{sm.label}</span></td>
                      <td>
                        <span className={`badge ${rm.cls}`}>
                          <span className={`status-dot ${rm.dot}`} />
                          {job.risk_level}
                        </span>
                      </td>
                      <td>
                        {job.confidence_score > 0 ? (
                          <span className="font-mono text-sm"
                            style={{ color: job.confidence_score >= 0.8 ? '#1a6f1a' : 'var(--warning-deep)' }}>
                            {Math.round(job.confidence_score * 100)}%
                          </span>
                        ) : <span className="text-muted text-sm">—</span>}
                      </td>
                      <td><span className="text-xs text-muted">{formatDate(job.created_at)}</span></td>
                      <td style={{ textAlign: 'right' }}>
                        <Link to={`/jobs/${job.id}`} className="btn btn-ghost btn-sm">
                          <ArrowRight size={13} />
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
};
