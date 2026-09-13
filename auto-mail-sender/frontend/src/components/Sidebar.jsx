import React from 'react';
import { LayoutDashboard, GitBranch, MailCheck, BarChart2, UserCog, LogOut } from 'lucide-react';
import { useAuth } from '../context/AuthContext';

export const Sidebar = ({ currentView, switchView, inboxBadgeCount = 0 }) => {
  const { user, logout } = useAuth();
  const avatarLetter = user?.full_name ? user.full_name[0].toUpperCase() : (user?.username ? user.username[0].toUpperCase() : 'U');

  const navItems = [
    { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
    { id: 'sequences', label: 'Sequences', icon: GitBranch },
    { id: 'inbox', label: 'Inbox', icon: MailCheck, badge: inboxBadgeCount },
    { id: 'analytics', label: 'Analytics', icon: BarChart2 },
    { id: 'settings', label: 'Settings', icon: UserCog },
  ];

  return (
    <aside className="sidebar">
      <div className="logo-section">
        <h1>
          <img src="/favicon.jpeg" alt="Logo" /> EISTATECH
        </h1>
        <p>Sequence Outreach</p>
      </div>
      <nav className="nav-menu">
        {navItems.map((item) => {
          const IconComp = item.icon;
          const isActive = currentView === item.id;
          return (
            <div
              key={item.id}
              className={`nav-item ${isActive ? 'active' : ''}`}
              onClick={() => switchView(item.id)}
            >
              <IconComp size={18} /> {item.label}
              {item.badge !== undefined && (
                <span className={`nav-badge ${item.badge > 0 ? '' : 'hidden'}`}>
                  {item.badge}
                </span>
              )}
            </div>
          );
        })}
      </nav>
      <div className="sidebar-footer">
        <div style={{ fontWeight: 600, fontSize: '0.7rem', color: 'var(--text-muted)', opacity: 0.8 }}>
          v2.0 • React SPA
        </div>
        {user && (
          <div className="user-pill">
            <div className="user-info">
              <div className="user-avatar">{avatarLetter}</div>
              <span className="user-name">{user.full_name || user.username}</span>
            </div>
            <button
              onClick={logout}
              className="logout-btn"
              title="Logout"
              style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
            >
              <LogOut size={16} />
            </button>
          </div>
        )}
      </div>
    </aside>
  );
};
