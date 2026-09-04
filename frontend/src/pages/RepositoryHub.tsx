import React, { useEffect, useState, useCallback } from 'react';
import { Link } from 'react-router-dom';
import {
  GitBranch, Network, Play, Plus, RefreshCw, Server, Trash2, X,
} from 'lucide-react';
import type { RepositoryItem, GitHubRepoItem, OpenPRItem } from '../api/client';
import {
  fetchTrackedRepos, fetchGitHubRepos, registerRepo, unregisterRepo,
  setMonitoring, setAutoRepair, triggerGraphBuild, triggerReview, fetchGraphStatus, fetchOpenPRs,
} from '../api/client';
import { useAuth } from '../context/AuthContext';
import { GitHubIcon } from '../components/GitHubIcon';

/* ─── Discover Modal ────────────────────────────────────────────────────────── */
interface DiscoverModalProps {
  onClose: () => void;
  onRegister: (fullName: string) => Promise<void>;
  trackedNames: Set<string>;
}

const DiscoverModal: React.FC<DiscoverModalProps> = ({ onClose, onRegister, trackedNames }) => {
  const [repos, setRepos] = useState<GitHubRepoItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [registering, setRegistering] = useState<string | null>(null);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    fetchGitHubRepos()
      .then(setRepos)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  const filtered = repos.filter(r =>
    r.full_name.toLowerCase().includes(filter.toLowerCase())
  );

  return (
    <div className="modal-backdrop" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header">
          <h2 className="modal-title">Add Repository</h2>
          <button className="btn btn-ghost btn-sm" onClick={onClose}><X size={14} /></button>
        </div>

        <input
          className="input input-sm mb-4"
          placeholder="Filter repositories..."
          value={filter}
          onChange={e => setFilter(e.target.value)}
        />

        {loading ? (
          <div style={{ padding: 'var(--space-xl)', textAlign: 'center', color: 'var(--mute)' }}>
            Loading repositories...
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 360, overflowY: 'auto' }}>
            {filtered.map(repo => {
              const tracked = trackedNames.has(repo.full_name);
              return (
                <div key={repo.id}
                  style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: 'var(--space-sm)',
                    borderRadius: 'var(--radius-sm)',
                    border: '1px solid var(--hairline)',
                    background: tracked ? 'var(--canvas-soft)' : 'var(--canvas)',
                  }}>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 500, color: tracked ? 'var(--mute)' : 'var(--ink)', letterSpacing: '-0.28px' }}>
                      {repo.full_name}
                    </div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--mute)', marginTop: 2 }}>
                      {repo.default_branch} &middot; {repo.private ? 'private' : 'public'}
                    </div>
                  </div>
                  {tracked ? (
                    <span className="badge badge-success">Tracked</span>
                  ) : (
                    <button
                      className="btn btn-primary btn-sm"
                      disabled={registering === repo.full_name}
                      onClick={async () => {
                        setRegistering(repo.full_name);
                        await onRegister(repo.full_name);
                        setRegistering(null);
                      }}
                    >
                      {registering === repo.full_name ? (
                        <RefreshCw size={12} className="spin" />
                      ) : (
                        <Plus size={12} />
                      )}
                      Track
                    </button>
                  )}
                </div>
              );
            })}
            {filtered.length === 0 && (
              <p style={{ textAlign: 'center', color: 'var(--mute)', fontSize: 14, padding: 'var(--space-lg)' }}>
                No repositories match.
              </p>
            )}
          </div>
        )}

        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
};

/* ─── PR Dropdown ───────────────────────────────────────────────────────────── */
const PRList: React.FC<{ repoId: string; onDispatch: (pr: number) => void }> = ({ repoId, onDispatch }) => {
  const [prs, setPrs] = useState<OpenPRItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchOpenPRs(repoId)
      .then(setPrs)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [repoId]);

  if (loading) {
    return (
      <div style={{ padding: 'var(--space-sm)', color: 'var(--mute)', fontSize: 12, fontFamily: 'var(--font-mono)' }}>
        Loading open PRs...
      </div>
    );
  }

  if (prs.length === 0) {
    return (
      <div style={{ padding: 'var(--space-sm)', color: 'var(--mute)', fontSize: 12 }}>
        No open pull requests found.
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 200, overflowY: 'auto' }}>
      {prs.map(pr => (
        <div
          key={pr.number}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '6px 10px', borderRadius: 'var(--radius-sm)',
            border: '1px solid var(--hairline)', background: 'var(--canvas-soft)',
          }}
        >
          <div style={{ minWidth: 0, marginRight: 8 }}>
            <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--ink)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              #{pr.number} {pr.title}
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--mute)' }}>
              by {pr.user} &middot; {pr.head_branch}
            </div>
          </div>
          <button className="btn btn-primary btn-sm" onClick={() => onDispatch(pr.number)}>
            <Play size={11} />
            Review
          </button>
        </div>
      ))}
    </div>
  );
};

/* ─── Repo Card ─────────────────────────────────────────────────────────────── */
interface GraphStatus { status: string; node_count: number; edge_count: number; }

const RepoCard: React.FC<{
  repo: RepositoryItem;
  onRemove: (id: string) => void;
  onMonitoringChange: (id: string, enabled: boolean) => void;
  onAutoRepairChange: (id: string, enabled: boolean) => void;
}> = ({ repo, onRemove, onMonitoringChange, onAutoRepairChange }) => {
  const [graphStatus, setGraphStatus] = useState<GraphStatus | null>(null);
  const [building, setBuilding] = useState(false);
  const [showPRs, setShowPRs] = useState(false);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const checkStatus = async () => {
      try {
        const res = await fetchGraphStatus(repo.id);
        setGraphStatus(res);
        if (res.status === 'BUILDING') {
          timer = setTimeout(checkStatus, 2500);
        }
      } catch {
        // ignore
      }
    };
    checkStatus();
    return () => clearTimeout(timer);
  }, [repo.id]);

  const handleBuildGraph = async () => {
    setBuilding(true);
    try {
      await triggerGraphBuild(repo.id, repo.default_branch);
      setGraphStatus({ status: 'BUILDING', node_count: 0, edge_count: 0 });
      const poll = setInterval(async () => {
        try {
          const res = await fetchGraphStatus(repo.id);
          setGraphStatus(res);
          if (res.status === 'READY') {
            clearInterval(poll);
            setBuilding(false);
          }
        } catch {
          // ignore
        }
      }, 2500);
    } catch (e) {
      console.error(e);
      setBuilding(false);
    }
  };

  const handleDispatch = async (prNumber: number) => {
    try { await triggerReview(repo.id, prNumber); }
    catch (e) { console.error(e); }
    finally { setShowPRs(false); }
  };

  const graphStatusBadge = () => {
    if (!graphStatus) return <span className="badge badge-default">Unknown</span>;
    if (graphStatus.status === 'READY')    return <span className="badge badge-success">Graph Ready</span>;
    if (graphStatus.status === 'BUILDING') return <span className="badge badge-running">Building</span>;
    if (graphStatus.status === 'NOT_BUILT')return <span className="badge badge-default">Not Built</span>;
    return null;
  };

  return (
    <div className="card" style={{ marginBottom: 'var(--space-md)' }}>
      <div className="card-inner">
        {/* Top row */}
        <div className="flex items-start justify-between gap-4 wrap">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span style={{ fontSize: 15, fontWeight: 600, color: 'var(--ink)', letterSpacing: '-0.28px' }}>
                {repo.full_name}
              </span>
              {graphStatusBadge()}
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--mute)' }}>
              <GitBranch size={10} style={{ display: 'inline', marginRight: 4 }} />
              {repo.default_branch}
              {graphStatus && graphStatus.status === 'READY' && (
                <span style={{ marginLeft: 12 }}>
                  {graphStatus.node_count} nodes &middot; {graphStatus.edge_count} edges
                </span>
              )}
            </div>
          </div>

          {/* Toggles */}
          <div className="flex items-center gap-4 wrap">
            <div className="flex items-center gap-2" title="Automatically dispatch reviews on new pull requests">
              <span style={{ fontSize: 12, color: 'var(--mute)', fontFamily: 'var(--font-mono)' }}>
                Auto-review
              </span>
              <input
                type="checkbox"
                className="toggle"
                checked={repo.monitoring_enabled ?? false}
                onChange={e => onMonitoringChange(repo.id, e.target.checked)}
              />
            </div>
            <div className="flex items-center gap-2" title="When enabled, patches are generated automatically for all repairable issues. When disabled, you decide which issues to repair manually.">
              <span style={{ fontSize: 12, color: repo.auto_repair_enabled ? '#107c41' : 'var(--mute)', fontFamily: 'var(--font-mono)' }}>
                {repo.auto_repair_enabled ? 'Auto-repair ON' : 'Manual Repair'}
              </span>
              <input
                type="checkbox"
                className="toggle"
                checked={repo.auto_repair_enabled ?? false}
                onChange={e => onAutoRepairChange(repo.id, e.target.checked)}
              />
            </div>
          </div>
        </div>

        {/* Actions */}
        <div className="divider" style={{ margin: 'var(--space-md) 0' }} />
        <div className="flex items-center gap-2 wrap">
          <button
            className="btn btn-secondary btn-sm"
            onClick={handleBuildGraph}
            disabled={building || graphStatus?.status === 'BUILDING'}
          >
            {building ? <RefreshCw size={12} className="spin" /> : <RefreshCw size={12} />}
            Build Graph
          </button>

          {graphStatus?.status === 'READY' && (
            <Link to={`/repos/${repo.id}/graph`} className="btn btn-secondary btn-sm">
              <Network size={12} />
              View Graph
            </Link>
          )}

          <button
            className="btn btn-secondary btn-sm"
            onClick={() => setShowPRs(prev => !prev)}
          >
            <Play size={12} />
            {showPRs ? 'Hide PRs' : 'Open PRs'}
          </button>

          <button
            className="btn btn-danger btn-sm"
            style={{ marginLeft: 'auto' }}
            onClick={() => onRemove(repo.id)}
          >
            <Trash2 size={12} />
            Remove
          </button>
        </div>

        {/* PR list */}
        {showPRs && (
          <div style={{ marginTop: 'var(--space-md)' }}>
            <p className="section-eyebrow" style={{ marginBottom: 'var(--space-xs)' }}>Open Pull Requests</p>
            <PRList repoId={repo.id} onDispatch={handleDispatch} />
          </div>
        )}
      </div>
    </div>
  );
};

/* ─── Repository Hub ────────────────────────────────────────────────────────── */
export const RepositoryHub: React.FC = () => {
  const { user, isAuthenticated, login } = useAuth();
  const [repos, setRepos] = useState<RepositoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [showDiscover, setShowDiscover] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try { setRepos(await fetchTrackedRepos()); }
    catch (e) { console.error(e); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleRegister = async (fullName: string) => {
    await registerRepo(fullName);
    await load();
  };

  const handleRemove = async (id: string) => {
    await unregisterRepo(id);
    setRepos(prev => prev.filter(r => r.id !== id));
  };

  const handleMonitoring = async (id: string, enabled: boolean) => {
    await setMonitoring(id, enabled);
    setRepos(prev => prev.map(r => r.id === id ? { ...r, monitoring_enabled: enabled } : r));
  };

  const handleAutoRepair = async (id: string, enabled: boolean) => {
    await setAutoRepair(id, enabled);
    setRepos(prev => prev.map(r => r.id === id ? { ...r, auto_repair_enabled: enabled } : r));
  };

  const trackedNames = new Set(repos.map(r => r.full_name));
  const monitoring  = repos.filter(r => r.monitoring_enabled).length;
  const autoRepairCount = repos.filter(r => r.auto_repair_enabled).length;

  return (
    <>
      {/* Auth Banner for unauthenticated visitors */}
      {!isAuthenticated && (
        <div className="auth-banner mb-6">
          <div className="auth-banner-content">
            <div className="auth-banner-icon-wrapper">
              <GitHubIcon size={24} />
            </div>
            <div>
              <h2 className="auth-banner-title">Sign in with GitHub</h2>
              <p className="auth-banner-description">
                Sign in with your GitHub account to discover repositories, track PRs, and configure autonomous reviews with isolated tenant security.
              </p>
            </div>
          </div>
          <button onClick={login} className="github-signin-btn" type="button">
            <GitHubIcon size={16} />
            <span>Sign in with GitHub</span>
          </button>
        </div>
      )}

      {/* Header */}
      <div className="page-header flex items-center justify-between">
        <div>
          <h1 className="page-title">Repositories</h1>
          <p className="page-subtitle">
            {isAuthenticated && user
              ? `Tracking repositories for @${user.username}`
              : 'Manage tracked repositories, monitoring, repair modes, and knowledge graphs.'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={load} className="btn btn-secondary btn-sm" disabled={loading}>
            <RefreshCw size={13} className={loading ? 'spin' : ''} />
            Refresh
          </button>
          <button className="btn btn-primary btn-sm" onClick={() => setShowDiscover(true)}>
            <Plus size={13} />
            Add Repository
          </button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid-stats">
        <div className="stat-card">
          <p className="stat-label">Tracked</p>
          <p className="stat-value">{repos.length}</p>
          <p className="stat-meta">Registered repositories</p>
        </div>
        <div className="stat-card">
          <p className="stat-label">Monitoring</p>
          <p className="stat-value" style={{ color: monitoring > 0 ? '#107c41' : 'var(--ink)' }}>{monitoring}</p>
          <p className="stat-meta">Auto-review enabled</p>
        </div>
        <div className="stat-card">
          <p className="stat-label">Auto-Repair</p>
          <p className="stat-value" style={{ color: autoRepairCount > 0 ? '#0070f3' : 'var(--mute)' }}>{autoRepairCount}</p>
          <p className="stat-meta">{repos.length - autoRepairCount} in manual repair mode</p>
        </div>
      </div>

      {/* Repo list */}
      <div>
        <p className="section-eyebrow" style={{ marginBottom: 'var(--space-md)' }}>Tracked Repositories</p>

        {loading ? (
          <div style={{ color: 'var(--mute)', fontSize: 14, padding: 'var(--space-lg)' }}>
            Loading...
          </div>
        ) : repos.length === 0 ? (
          <div className="empty-state">
            <Server size={32} />
            <span className="empty-state-title">No repositories tracked.</span>
            <span className="empty-state-body">
              {isAuthenticated
                ? 'Click "Add Repository" to select a repository from your GitHub account.'
                : 'Sign in with GitHub to view and track your repositories.'}
            </span>
          </div>
        ) : (
          repos.map(repo => (
            <RepoCard
              key={repo.id}
              repo={repo}
              onRemove={handleRemove}
              onMonitoringChange={handleMonitoring}
              onAutoRepairChange={handleAutoRepair}
            />
          ))
        )}
      </div>

      {/* Discover modal */}
      {showDiscover && (
        <DiscoverModal
          onClose={() => setShowDiscover(false)}
          onRegister={handleRegister}
          trackedNames={trackedNames}
        />
      )}
    </>
  );
};
