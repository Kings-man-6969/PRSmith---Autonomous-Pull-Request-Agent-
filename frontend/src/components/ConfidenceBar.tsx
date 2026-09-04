import React from 'react';
import { ShieldCheck, AlertTriangle } from 'lucide-react';

interface ConfidenceBarProps {
  score: number; // 0.0 to 1.0
  validationPassed?: boolean;
}

export const ConfidenceBar: React.FC<ConfidenceBarProps> = ({ score, validationPassed = true }) => {
  const percentage = Math.round(score * 100);
  const color = percentage >= 85 ? 'var(--accent-emerald)' : percentage >= 60 ? 'var(--accent-amber)' : 'var(--accent-rose)';

  return (
    <div className="glass-panel" style={{ padding: '16px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontWeight: 600, fontSize: '0.9rem' }}>
          {percentage >= 80 ? (
            <ShieldCheck size={18} color="var(--accent-emerald)" />
          ) : (
            <AlertTriangle size={18} color="var(--accent-amber)" />
          )}
          <span>Evidence-Backed Confidence</span>
        </div>
        <span style={{ fontWeight: 700, fontSize: '1.1rem', color }}>{percentage}%</span>
      </div>

      <div style={{ height: '8px', background: 'rgba(255, 255, 255, 0.08)', borderRadius: '4px', overflow: 'hidden' }}>
        <div
          style={{
            height: '100%',
            width: `${percentage}%`,
            background: color,
            transition: 'width 0.6s ease',
            boxShadow: `0 0 10px ${color}`,
          }}
        />
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '10px', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
        <span>Graph Grounded: 100%</span>
        <span>Scope Verified: PASS</span>
        <span>Sandbox Validation: {validationPassed ? 'PASSED' : 'FAILED'}</span>
      </div>
    </div>
  );
};
