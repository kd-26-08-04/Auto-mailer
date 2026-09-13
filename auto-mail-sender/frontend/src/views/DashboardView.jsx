import React, { useState, useEffect } from 'react';
import { GitBranch, Terminal } from 'lucide-react';
import { api } from '../api';

export const DashboardView = ({ onNavigateSequences, onShowCreateSequence, sequenceId }) => {
  const [dashboardData, setDashboardData] = useState(null);
  const [statusData, setStatusData] = useState(null);
  const [activeTab, setActiveTab] = useState('active');
  const [loading, setLoading] = useState(true);

  const fetchDashboard = async () => {
    try {
      const data = await api.getSequenceDashboard(sequenceId);
      if (data.success) {
        setDashboardData(data);
      }
      const st = await api.getStatus();
      setStatusData(st);
    } catch (err) {
      console.error('Dashboard fetch error:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDashboard();
    const interval = setInterval(fetchDashboard, 3000);
    return () => clearInterval(interval);
  }, [sequenceId]);

  const hasSequence = dashboardData?.sequence != null;
  const seq = dashboardData?.sequence;
  const stats = dashboardData?.stats || {};
  const lists = dashboardData?.lists || {};
  const logs = statusData?.logs || [];

  const recipientRow = (rec, idx) => (
    <div key={idx} className="recipient-item">
      <span>
        <span className="status-dot"></span>
        {rec.email}
      </span>
      <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>
        Step {rec.current_step} · {rec.next_send_at ? new Date(rec.next_send_at).toLocaleString() : (rec.status || 'Pending')}
      </span>
    </div>
  );

  return (
    <section id="view-dashboard" className="view-pane">
      {!loading && !hasSequence && (
        <div id="no-sequence-banner" className="card" style={{ textAlign: 'center', padding: '2rem' }}>
          <p style={{ color: 'var(--text-muted)', marginBottom: '1rem' }}>
            No sequences yet. Create one to start outreach.
          </p>
          <button
            className="btn-primary"
            style={{ width: 'auto' }}
            onClick={() => {
              onNavigateSequences();
              onShowCreateSequence();
            }}
          >
            Create Sequence
          </button>
        </div>
      )}

      {hasSequence && (
        <div id="dashboard-content" className="content-stack">
          {/* STATS GRID */}
          <div className="stats-grid">
            <div className="card stat-card">
              <div className="stat-label">Sent Today</div>
              <div id="stat-sent" className="stat-value">
                {stats.sent_today || statusData?.sent_count || 0}
              </div>
            </div>
            <div className="card stat-card">
              <div className="stat-label">Active</div>
              <div id="stat-active" className="stat-value" style={{ color: 'var(--primary)' }}>
                {stats.active || 0}
              </div>
            </div>
            <div className="card stat-card">
              <div className="stat-label">Opened</div>
              <div id="stat-opened" className="stat-value" style={{ color: 'var(--success)' }}>
                {stats.opened || statusData?.opened_count || 0}
              </div>
            </div>
            <div className="card stat-card">
              <div className="stat-label">Clicked</div>
              <div id="stat-clicked" className="stat-value" style={{ color: 'var(--info)' }}>
                {stats.clicked || statusData?.clicked_count || 0}
              </div>
            </div>
            <div className="card stat-card">
              <div className="stat-label">Replied</div>
              <div id="stat-replied" className="stat-value" style={{ color: '#8b5cf6' }}>
                {stats.replied || statusData?.replied_count || 0}
              </div>
            </div>
            <div className="card stat-card">
              <div className="stat-label">Completed</div>
              <div id="stat-completed" className="stat-value">
                {stats.completed || 0}
              </div>
            </div>
            <div className="card stat-card">
              <div className="stat-label">Failed</div>
              <div id="stat-failed" className="stat-value" style={{ color: 'var(--danger)' }}>
                {stats.failed || statusData?.failed_count || 0}
              </div>
            </div>
          </div>

          {/* SEQUENCE CARD */}
          <div className="card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h2>
                <GitBranch size={20} style={{ display: 'inline', marginRight: 8 }} />
                <span>{seq.name}</span>
              </h2>
              <span className={`badge ${seq.status === 'active' ? 'badge-running' : 'badge-idle'}`}>
                {seq.status}
              </span>
            </div>
            <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>
              {seq.steps?.length || 0} step(s) · {stats.enrolled || 0} enrolled contacts
            </p>
          </div>

          {/* CONTACTS LIST TABS */}
          <div className="card">
            <div className="tabs">
              <div className={`tab ${activeTab === 'active' ? 'active' : ''}`} onClick={() => setActiveTab('active')}>
                In Progress ({lists.active?.length || 0})
              </div>
              <div className={`tab ${activeTab === 'completed' ? 'active' : ''}`} onClick={() => setActiveTab('completed')}>
                Completed ({lists.completed?.length || 0})
              </div>
              <div className={`tab ${activeTab === 'replied' ? 'active' : ''}`} onClick={() => setActiveTab('replied')}>
                Replied ({lists.replied?.length || 0})
              </div>
              <div className={`tab ${activeTab === 'failed' ? 'active' : ''}`} onClick={() => setActiveTab('failed')}>
                Failed ({lists.failed?.length || 0})
              </div>
            </div>

            <div className="tab-content recipient-list" style={{ display: 'block' }}>
              {(lists[activeTab] || []).length === 0 ? (
                <p style={{ padding: '1rem', color: 'var(--text-muted)' }}>No contacts in this category</p>
              ) : (
                (lists[activeTab] || []).map(recipientRow)
              )}
            </div>
          </div>

          {/* ACTIVITY LOG */}
          <div className="card">
            <h2>
              <Terminal size={20} style={{ display: 'inline', marginRight: 8 }} /> Activity Log
            </h2>
            <div id="log-container">
              {logs.length === 0 ? (
                <div style={{ opacity: 0.6 }}>System ready. Logs will appear here...</div>
              ) : (
                logs.map((logLine, idx) => <div key={idx}>{logLine}</div>)
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
};
