import React, { useState } from 'react';
import { Bug, CheckCircle2, ChevronDown, ChevronUp, Link2, MapPin, RefreshCw, ShieldAlert, Wrench } from 'lucide-react';
import type { ReviewFindingData } from '../api/client';

interface FindingCardProps {
  finding: ReviewFindingData;
  onRepair?: (findingId: string) => Promise<void>;
  isRepairing?: boolean;
}

const severityBadge = (s: string) => {
  const map: Record<string, string> = { CRITICAL: 'badge-error', HIGH: 'badge-error', MEDIUM: 'badge-warning' };
  return map[s] ?? 'badge-default';
};

export const FindingCard: React.FC<FindingCardProps> = ({ finding, onRepair, isRepairing }) => {
  const [expanded, setExpanded] = useState(false);
  const isHigh = finding.severity === 'CRITICAL' || finding.severity === 'HIGH';
  const isRepaired = finding.status === 'REPAIRED';
  const canRepair = ['HIGH', 'MEDIUM'].includes(finding.repairability?.toUpperCase()) && !isRepaired;

  return (
    <div className="card" style={{ marginBottom: 'var(--space-xs)' }}>
      <div className="card-inner" style={{ padding: 'var(--space-md)' }}>
        {/* Header row */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-3">
            <div style={{ marginTop: 1, color: isHigh ? 'var(--error)' : 'var(--warning)', flexShrink: 0 }}>
              {finding.category === 'SECURITY' ? <ShieldAlert size={16} /> : <Bug size={16} />}
            </div>
            <div>
              <div className="flex items-center gap-2 wrap">
                <span className="font-mono text-xs text-muted">{finding.finding_id}</span>
                <span className={`badge ${severityBadge(finding.severity)}`}>{finding.severity}</span>
                <span className="badge badge-default">{finding.category}</span>
                <span className="text-xs text-muted">
                  Repairability:{' '}
                  <strong style={{ color: finding.repairability === 'HIGH' ? '#107c41' : 'var(--ink)' }}>
                    {finding.repairability}
                  </strong>
                </span>
                {isRepaired && (
                  <span className="badge badge-success" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    <CheckCircle2 size={11} /> Repaired
                  </span>
                )}
              </div>
              <p style={{ marginTop: 'var(--space-xxs)', fontSize: 14, color: 'var(--ink)', letterSpacing: '-0.28px' }}>
                {finding.description}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2" style={{ flexShrink: 0 }}>
            {canRepair && onRepair && (
              <button
                className="btn btn-primary btn-sm"
                disabled={isRepairing}
                onClick={() => onRepair(finding.id)}
                title="Trigger automated LLM patch repair for this specific issue"
              >
                {isRepairing ? <RefreshCw size={11} className="spin" /> : <Wrench size={11} />}
                Repair Issue
              </button>
            )}

            <button
              onClick={() => setExpanded(!expanded)}
              className="btn-ghost btn"
              style={{ padding: '4px', height: 'auto' }}
            >
              {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
          </div>
        </div>

        {/* File location */}
        <div className="flex items-center gap-1 mt-2"
          style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--mute)' }}>
          <MapPin size={11} />
          {finding.file_path}
          {finding.symbol_name && <span style={{ color: 'var(--body)' }}> &rarr; {finding.symbol_name}</span>}
        </div>

        {/* Expanded evidence */}
        {expanded && (
          <div style={{ marginTop: 'var(--space-md)', paddingTop: 'var(--space-md)', borderTop: '1px solid var(--hairline)' }}>
            <p className="section-eyebrow" style={{ marginBottom: 'var(--space-xs)' }}>Knowledge Graph Evidence</p>
            {finding.evidence && finding.evidence.length > 0 ? (
              <div className="flex flex-col gap-1">
                {finding.evidence.map((ev, idx) => (
                  <div key={idx}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 8,
                      padding: '6px 10px', borderRadius: 'var(--radius-sm)',
                      background: 'var(--canvas-soft-2)', border: '1px solid var(--hairline)',
                    }}>
                    <Link2 size={12} style={{ color: 'var(--link)', flexShrink: 0 }} />
                    <span className="text-xs text-muted">{ev.entity_type}:</span>
                    <span className="font-mono text-xs">{ev.entity_name}</span>
                    <span className="text-xs text-muted">({ev.relationship})</span>
                    {ev.verified_in_graph ? (
                      <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 4, color: '#107c41', fontSize: 12 }}>
                        <CheckCircle2 size={11} /> Verified
                      </span>
                    ) : (
                      <span style={{ marginLeft: 'auto', color: 'var(--error)', fontSize: 12 }}>Ungrounded</span>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-muted">Direct diff inspection — no graph evidence required.</p>
            )}

            {finding.affected_entities?.length > 0 && (
              <div style={{ marginTop: 'var(--space-sm)' }}>
                <p className="section-eyebrow" style={{ marginBottom: 'var(--space-xxs)' }}>Downstream Entities</p>
                <div className="flex gap-1 wrap">
                  {finding.affected_entities.map((ae, idx) => (
                    <span key={idx} className="badge badge-info font-mono">{ae}</span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
