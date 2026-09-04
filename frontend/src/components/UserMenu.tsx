import React, { useState, useRef, useEffect } from 'react';
import { LogOut, User as UserIcon, ChevronDown } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { GitHubIcon } from './GitHubIcon';

export const UserMenu: React.FC<{ compact?: boolean }> = ({ compact = false }) => {
  const { user, isAuthenticated, loading, login, logout } = useAuth();
  const [isOpen, setIsOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  if (loading) {
    return (
      <div className="user-menu-skeleton">
        <div className="skeleton-avatar" />
      </div>
    );
  }

  if (!isAuthenticated || !user) {
    return (
      <button
        onClick={login}
        className="github-signin-btn"
        title="Sign in with GitHub"
        type="button"
      >
        <GitHubIcon size={16} className="github-icon" />
        <span>Sign in with GitHub</span>
      </button>
    );
  }

  return (
    <div className={`user-menu-container ${compact ? 'compact' : ''}`} ref={menuRef}>
      <button
        type="button"
        onClick={() => setIsOpen((prev) => !prev)}
        className="user-menu-trigger"
        aria-expanded={isOpen}
      >
        {user.avatar_url ? (
          <img
            src={user.avatar_url}
            alt={user.username}
            className="user-avatar"
          />
        ) : (
          <div className="user-avatar-placeholder">
            <UserIcon size={16} />
          </div>
        )}
        {!compact && (
          <div className="user-info">
            <span className="user-username">@{user.username}</span>
            <span className="user-role-badge">{user.role}</span>
          </div>
        )}
        <ChevronDown size={14} className={`chevron-icon ${isOpen ? 'open' : ''}`} />
      </button>

      {isOpen && (
        <div className="user-menu-dropdown animate-fade-in">
          <div className="dropdown-header">
            <p className="dropdown-user-name">@{user.username}</p>
            {user.email && <p className="dropdown-user-email">{user.email}</p>}
          </div>
          <div className="dropdown-divider" />
          <button
            type="button"
            className="dropdown-item logout-btn"
            onClick={async () => {
              setIsOpen(false);
              await logout();
            }}
          >
            <LogOut size={15} />
            <span>Sign Out</span>
          </button>
        </div>
      )}
    </div>
  );
};
