import React from 'react';
import { Inbox, Sun, Moon } from 'lucide-react';

const titles = {
  dashboard: 'Dashboard',
  sequences: 'Sequences',
  inbox: 'Inbox',
  analytics: 'Analytics',
  settings: 'Settings',
};

export const Header = ({ currentView, isDarkMode, toggleTheme, onScanReplies, status = 'idle', scanning = false }) => {
  const getBadgeClass = (st) => {
    switch (st) {
      case 'running':
      case 'active':
        return 'badge-running';
      case 'completed':
        return 'badge-success';
      case 'error':
        return 'badge-danger';
      default:
        return 'badge-idle';
    }
  };

  return (
    <header>
      <div>
        <h2 id="page-title">{titles[currentView] || 'Dashboard'}</h2>
      </div>
      <div className="header-actions" style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
        <button
          id="check-replies-btn"
          className="btn-secondary"
          disabled={scanning}
          onClick={onScanReplies}
          style={{ width: 'auto', display: 'inline-flex', alignItems: 'center', gap: '0.45rem', padding: '0.5rem 0.95rem', fontSize: '0.85rem' }}
        >
          <Inbox size={16} /> {scanning ? 'Scanning...' : 'Scan Replies'}
        </button>
        <button
          id="theme-toggle-btn"
          className="btn-secondary"
          onClick={toggleTheme}
          style={{ width: '36px', height: '36px', padding: 0, borderRadius: '50%', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}
          title="Toggle dark mode"
        >
          {isDarkMode ? <Sun size={18} /> : <Moon size={18} />}
        </button>
        <div id="app-status-badge" className={`badge ${getBadgeClass(status)}`}>
          {status}
        </div>
      </div>
    </header>
  );
};
