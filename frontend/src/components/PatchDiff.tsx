import React, { useState } from 'react';
import { Check, Copy, FileCode2 } from 'lucide-react';

interface PatchDiffProps {
  diffContent: string;
  filesChanged?: string[];
}

export const PatchDiff: React.FC<PatchDiffProps> = ({ diffContent, filesChanged = [] }) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(diffContent);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const lines = diffContent.split('\n');

  return (
    <div className="glass-panel" style={{ overflow: 'hidden' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '10px 16px',
          background: 'rgba(0, 0, 0, 0.3)',
          borderBottom: '1px solid var(--border-color)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '0.85rem', fontWeight: 600 }}>
          <FileCode2 size={16} color="var(--accent-indigo)" />
          <span>Verified Git Unified Diff</span>
          {filesChanged.length > 0 && (
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>({filesChanged.length} files)</span>
          )}
        </div>
        <button
          onClick={handleCopy}
          className="btn"
          style={{ padding: '4px 10px', fontSize: '0.75rem', background: 'rgba(255,255,255,0.06)', color: 'var(--text-secondary)' }}
        >
          {copied ? <Check size={14} color="var(--accent-emerald)" /> : <Copy size={14} />}
          {copied ? 'Copied' : 'Copy Diff'}
        </button>
      </div>

      <div
        style={{
          padding: '12px 16px',
          maxHeight: '400px',
          overflowY: 'auto',
          fontFamily: 'var(--font-mono)',
          fontSize: '0.8rem',
          lineHeight: '1.4',
        }}
      >
        {lines.map((line, idx) => {
          let lineBg = 'transparent';
          let textColor = 'var(--text-secondary)';

          if (line.startsWith('+') && !line.startsWith('+++')) {
            lineBg = 'rgba(16, 185, 129, 0.12)';
            textColor = '#34d399';
          } else if (line.startsWith('-') && !line.startsWith('---')) {
            lineBg = 'rgba(244, 63, 94, 0.12)';
            textColor = '#fb7185';
          } else if (line.startsWith('@@')) {
            textColor = 'var(--accent-purple)';
            lineBg = 'rgba(168, 85, 247, 0.08)';
          }

          return (
            <div
              key={idx}
              style={{
                display: 'flex',
                background: lineBg,
                padding: '1px 4px',
                borderRadius: '2px',
              }}
            >
              <span style={{ width: '32px', userSelect: 'none', color: 'var(--text-muted)', textAlign: 'right', paddingRight: '10px' }}>
                {idx + 1}
              </span>
              <span style={{ color: textColor, whiteSpace: 'pre' }}>{line}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
};
