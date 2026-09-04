import React, { useEffect, useState, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, RefreshCw, ShieldCheck, Wrench } from 'lucide-react';
import type { JobDetailData, PatchData } from '../api/client';
import { fetchJobDetail, fetchJobPatches, triggerFindingRepair } from '../api/client';
import { ConfidenceBar } from '../components/ConfidenceBar';
import { FindingCard } from '../components/FindingCard';
import { ImpactGraph } from '../components/ImpactGraph';
import { PatchDiff } from '../components/PatchDiff';
import { ValidationReport } from '../components/ValidationReport';

const riskBadge = (risk: string) => {
  const map: Record<string, string> = { CRITICAL: 'badge-error', HIGH: 'badge-error', MEDIUM: 'badge-warning', LOW: 'badge-success' };
  return map[risk] ?? 'badge-default';
};

const statusBadge = (status: string) => {
  if (status === 'SUCCESS') return 'badge-success';
  if (status === 'ESCALATED') return 'badge-error';
  if (['REVIEWING', 'REPAIRING', 'SNAPSHOTTING'].includes(status)) return 'badge-running';
  return 'badge-default';
};

export const JobDetail: React.FC = () => {
  const { jobId } = useParams<{ jobId: string }>();
  const [job, setJob] = useState<JobDetailData | null>(null);
  const [patches, setPatches] = useState<PatchData[]>([]);
  const [loading, setLoading] = useState(true);
  const [repairingFindingId, setRepairingFindingId] = useState<string | null>(null);

  const reloadData = useCallback(async () => {
    if (!jobId) return;
    try {
      const [j, p] = await Promise.all([fetchJobDetail(jobId), fetchJobPatches(jobId)]);
      setJob(j);
      setPatches(p);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  useEffect(() => {
    reloadData();
  }, [reloadData]);

  const handleRepairFinding = async (findingId: string) => {
    if (!jobId) return;
    setRepairingFindingId(findingId);
    try {
      await triggerFindingRepair(jobId, findingId);
      // Poll until repair task finishes or patches refresh
      let count = 0;
      const interval = setInterval(async () => {
        count++;
        await reloadData();
        if (count >= 6) {
          clearInterval(interval);
          setRepairingFindingId(null);
        }
      }, 3000);
    } catch (e) {
      console.error("Repair dispatch failed", e);
      setRepairingFindingId(null);
    }
  };

  if (loading) {
    return (
      <div style={{ padding: 'var(--space-3xl)', textAlign: 'center', color: 'var(--mute)' }}>
        Loading job data...
      </div>
    );
  }

  if (!job) {
    return (
      <div style={{ padding: 'var(--space-3xl)', textAlign: 'center' }}>
        <p style={{ color: 'var(--body)', marginBottom: 'var(--space-md)' }}>Job not found.</p>
        <Link to="/" className="btn btn-secondary">Back to PR Jobs</Link>
      </div>
    );
  }

  const allFindings = job.review_runs.flatMap(r => r.findings);
  const validationResults = job.validation_runs.flatMap(v => v.results);
  const affectedSymbols = allFindings.flatMap(f => f.affected_entities);

  return (
    <>
      {/* Breadcrumb */}
      <div style={{ marginBottom: 'var(--space-lg)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <Link to="/"
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6,
            fontSize: 13, color: 'var(--body)', letterSpacing: '-0.28px' }}>
          <ArrowLeft size={13} />
          PR Jobs
        </Link>
        <button className="btn btn-ghost btn-sm" onClick={reloadData}>
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {/* Job header card */}
      <div className="card" style={{ marginBottom: 'var(--space-lg)' }}>
        <div className="card-inner">
          <div className="flex items-start justify-between wrap gap-4">
            <div>
              {/* Caption-mono eyebrow */}
              <p className="section-eyebrow">{job.repository}</p>
              <h1 className="t-display-md" style={{ marginTop: 'var(--space-xxs)' }}>
                {job.pr_title || `Pull Request #${job.pr_number}`}
              </h1>
              <div className="flex items-center gap-2 mt-2">
                <span className="font-mono text-xs text-muted">#{job.pr_number}</span>
                <span className={`badge ${statusBadge(job.status)}`}>{job.status}</span>
                <span className={`badge ${riskBadge(job.risk_level)}`}>Risk: {job.risk_level}</span>
              </div>
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--mute)', textAlign: 'right' }}>
              <div>base: {job.base_sha?.substring(0, 8) ?? '—'}</div>
              <div>head: {job.head_sha?.substring(0, 8) ?? '—'}</div>
            </div>
          </div>
        </div>
      </div>

      {/* Confidence */}
      <div style={{ marginBottom: 'var(--space-lg)' }}>
        <ConfidenceBar score={job.confidence_score || 0} validationPassed={job.status === 'SUCCESS'} />
      </div>

      {/* Two-column grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(380px, 1fr))', gap: 'var(--space-lg)', marginBottom: 'var(--space-lg)' }}>
        {/* Findings */}
        <div>
          <div className="flex items-center gap-2 mb-4">
            <ShieldCheck size={16} style={{ color: 'var(--body)' }} />
            <h2 style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', letterSpacing: '-0.28px' }}>
              Review Findings
            </h2>
            <span className="badge badge-default">{allFindings.length}</span>
          </div>

          {allFindings.length === 0 ? (
            <div className="card-soft" style={{ textAlign: 'center', color: 'var(--mute)', fontSize: 14 }}>
              No actionable findings. PR passes all review checks.
            </div>
          ) : (
            allFindings.map(f => (
              <FindingCard
                key={f.id}
                finding={f}
                onRepair={handleRepairFinding}
                isRepairing={repairingFindingId === f.id}
              />
            ))
          )}
        </div>

        {/* Impact + Validation */}
        <div className="flex flex-col gap-6">
          <ImpactGraph
            targetSymbol={allFindings[0]?.symbol_name || 'PR Scope'}
            affectedEntities={affectedSymbols}
          />
          {validationResults.length > 0 && <ValidationReport results={validationResults} />}
        </div>
      </div>

      {/* Patches */}
      {patches.length > 0 && (
        <>
          <div className="flex items-center gap-2 mb-4">
            <Wrench size={16} style={{ color: 'var(--body)' }} />
            <h2 style={{ fontSize: 14, fontWeight: 600, color: 'var(--ink)', letterSpacing: '-0.28px' }}>
              Generated Patches
            </h2>
            <span className="badge badge-default">{patches.length}</span>
          </div>
          {patches.map(p => (
            <div key={p.id} style={{ marginBottom: 'var(--space-md)' }}>
              <PatchDiff diffContent={p.diff_content} filesChanged={p.files_changed} />
            </div>
          ))}
        </>
      )}
    </>
  );
};
