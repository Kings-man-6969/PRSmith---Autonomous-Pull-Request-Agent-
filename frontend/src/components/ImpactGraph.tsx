import React from 'react';
import { GitCommit, Layers, Network, TestTube } from 'lucide-react';

interface ImpactGraphProps {
  targetSymbol: string;
  affectedEntities: string[];
}

export const ImpactGraph: React.FC<ImpactGraphProps> = ({ targetSymbol, affectedEntities }) => {
  return (
    <div className="glass-panel" style={{ padding: '16px' }}>
      <h3 style={{ fontSize: '0.95rem', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
        <Network size={18} color="var(--accent-purple)" />
        <span>Repository Impact Subgraph</span>
      </h3>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        <div
          style={{
            padding: '10px 14px',
            borderRadius: '8px',
            background: 'rgba(99, 102, 241, 0.15)',
            border: '1px solid rgba(99, 102, 241, 0.4)',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
          }}
        >
          <GitCommit size={16} color="var(--accent-indigo)" />
          <span style={{ fontSize: '0.85rem', fontWeight: 600 }}>Target Symbol:</span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.85rem', color: 'var(--accent-blue)' }}>
            {targetSymbol || 'Repository Scope'}
          </span>
        </div>

        {affectedEntities.length > 0 ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '8px' }}>
            {affectedEntities.map((entity, idx) => {
              const isTest = entity.startsWith('test:');
              const isApi = entity.startsWith('api:');

              return (
                <div
                  key={idx}
                  style={{
                    padding: '8px 12px',
                    borderRadius: '6px',
                    background: 'rgba(0, 0, 0, 0.25)',
                    border: '1px solid rgba(255, 255, 255, 0.05)',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    fontSize: '0.75rem',
                  }}
                >
                  {isTest ? (
                    <TestTube size={14} color="var(--accent-emerald)" />
                  ) : isApi ? (
                    <Layers size={14} color="var(--accent-amber)" />
                  ) : (
                    <Network size={14} color="var(--accent-blue)" />
                  )}
                  <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {entity}
                  </span>
                </div>
              );
            })}
          </div>
        ) : (
          <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>No external downstream blast radius detected.</div>
        )}
      </div>
    </div>
  );
};
