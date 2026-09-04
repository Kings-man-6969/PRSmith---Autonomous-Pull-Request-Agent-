import React from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import { GitPullRequest, Network, Server } from 'lucide-react';
import { Dashboard } from './pages/Dashboard';
import { JobDetail } from './pages/JobDetail';
import { RepositoryGraph } from './pages/RepositoryGraph';
import { RepositoryHub } from './pages/RepositoryHub';
import { AuthProvider } from './context/AuthContext';
import { UserMenu } from './components/UserMenu';

const NAV = [
  { to: '/',      label: 'PR Jobs',        icon: <GitPullRequest size={15} /> },
  { to: '/repos', label: 'Repositories',   icon: <Server size={15} /> },
  { to: '/graph', label: 'Knowledge Graph',icon: <Network size={15} /> },
];

const Sidebar: React.FC = () => {
  const { pathname } = useLocation();
  const isActive = (to: string) => (to === '/' ? pathname === '/' : pathname.startsWith(to));

  return (
    <aside className="sidebar">
      {/* Logo */}
      <div className="sidebar-logo">
        <div className="sidebar-logo-mark">
          {/* SVG monogram instead of Sparkles icon — clean, no emoji */}
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M2 12 L7 2 L12 12" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
            <path d="M4 8.5 L10 8.5" stroke="white" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
        </div>
        <span className="sidebar-logo-text">
          PRSmith
          <span className="sidebar-badge">v2</span>
        </span>
      </div>

      {/* Nav */}
      <nav className="sidebar-nav">
        <span className="sidebar-section-label">Navigation</span>
        {NAV.map(({ to, label, icon }) => (
          <Link key={to} to={to} className={`nav-item${isActive(to) ? ' active' : ''}`}>
            {icon}
            {label}
          </Link>
        ))}
      </nav>

      <div className="sidebar-footer">
        <UserMenu />
        <div className="sidebar-tagline">Autonomous code review &amp; repair</div>
      </div>
    </aside>
  );
};

const PAGE_TITLES: Record<string, string> = {
  '/':      'PR Jobs',
  '/repos': 'Repositories',
  '/graph': 'Knowledge Graph',
};

const Shell: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { pathname } = useLocation();
  const title = pathname.startsWith('/jobs/')
    ? 'Job Detail'
    : pathname.startsWith('/repos/') && pathname.endsWith('/graph')
    ? 'Knowledge Graph'
    : (PAGE_TITLES[pathname] ?? 'PRSmith');

  return (
    <div className="app-shell">
      <Sidebar />
      <div className="main-content">
        <header className="topbar">
          <span className="topbar-title">{title}</span>
          <div className="topbar-actions">
            <UserMenu compact />
          </div>
        </header>
        <main className="page">{children}</main>
      </div>
    </div>
  );
};

const AppRoutes: React.FC = () => (
  <Shell>
    <Routes>
      <Route path="/"                    element={<Dashboard />} />
      <Route path="/jobs/:jobId"         element={<JobDetail />} />
      <Route path="/repos"               element={<RepositoryHub />} />
      <Route path="/repos/:repoId/graph" element={<RepositoryGraph />} />
      <Route path="/graph"               element={<RepositoryGraph />} />
    </Routes>
  </Shell>
);

export const App: React.FC = () => (
  <BrowserRouter>
    <AuthProvider>
      <AppRoutes />
    </AuthProvider>
  </BrowserRouter>
);

export default App;
