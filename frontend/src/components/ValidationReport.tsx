import React, { useState } from 'react';
import { CheckCircle2, ChevronDown, ChevronRight, Terminal, XCircle } from 'lucide-react';

interface ValidationResultItem {
  layer: string;
  command: string;
  passed: boolean;
  exit_code: number;
  stdout: string;
  stderr: string;
  failure_class: string | null;
}

interface ValidationReportProps {
  results: ValidationResultItem[];
  stage?: string;
}

export const ValidationReport: React.FC<ValidationReportProps> = ({ results, stage = 'Sandbox Validation' }) => {
  const [expandedLayer, setExpandedLayer] = useState<string | null>(null);

  const toggleExpand = (layer: string) => {
    setExpandedLayer(expandedLayer === layer ? null : layer);
  };

  return (
    <div className="glass-panel" style={{ padding: '16px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
        <h3 style={{ fontSize: '0.95rem', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Terminal size={18} color="var(--accent-blue)" />
          <span>{stage} Layers</span>
        </h3>
        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
          {results.filter((r) => r.passed).length} / {results.length} Passed
        </span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        {results.map((item, idx) => (
          <div
            key={idx}
            style={{
              borderRadius: '8px',
              background: 'rgba(0, 0, 0, 0.25)',
              border: `1px solid ${item.passed ? 'rgba(16, 185, 129, 0.2)' : 'rgba(244, 63, 94, 0.3)'}`,
              overflow: 'hidden',
            }}
          >
            <div
              onClick={() => toggleExpand(item.layer)}
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: '10px 14px',
                cursor: 'pointer',
                userSelect: 'none',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                {item.passed ? (
                  <CheckCircle2 size={16} color="var(--accent-emerald)" />
                ) : (
                  <XCircle size={16} color="var(--accent-rose)" />
                )}
                <div>
                  <span style={{ fontWeight: 600, fontSize: '0.85rem' }}>{item.layer}</span>
                  <span style={{ marginLeft: '8px', fontFamily: 'var(--font-mono)', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                    `{item.command}`
                  </span>
                </div>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                {item.failure_class && (
                  <span style={{ fontSize: '0.75rem', padding: '2px 6px', borderRadius: '4px', background: 'rgba(244,63,94,0.15)', color: '#fb7185' }}>
                    {item.failure_class}
                  </span>
                )}
                {expandedLayer === item.layer ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
              </div>
            </div>

            {expandedLayer === item.layer && (
              <div
                style={{
                  padding: '12px 14px',
                  background: 'rgba(0, 0, 0, 0.4)',
                  borderTop: '1px solid rgba(255, 255, 255, 0.05)',
                  fontFamily: 'var(--font-mono)',
                  fontSize: '0.75rem',
                  maxHeight: '250px',
                  overflowY: 'auto',
                }}
              >
                {item.stdout && (
                  <div>
                    <div style={{ color: 'var(--text-muted)', marginBottom: '4px' }}>--- STDOUT ---</div>
                    <pre style={{ color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>{item.stdout}</pre>
                  </div>
                )}
                {item.stderr && (
                  <div style={{ marginTop: '8px' }}>
                    <div style={{ color: 'var(--accent-rose)', marginBottom: '4px' }}>--- STDERR ---</div>
                    <pre style={{ color: '#fda4af', whiteSpace: 'pre-wrap' }}>{item.stderr}</pre>
                  </div>
                )}
                {!item.stdout && !item.stderr && (
                  <div style={{ color: 'var(--text-muted)' }}>Process completed with exit code {item.exit_code} (no output).</div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
};
