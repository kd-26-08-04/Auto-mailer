import React, { useState, useEffect } from 'react';
import { GitBranch, Terminal, Play, RefreshCw, Clock, CheckCircle, AlertTriangle } from 'lucide-react';
import { api } from '../api';

export const DashboardView = ({ onNavigateSequences, onShowCreateSequence, sequenceId }) => {
  const [dashboardData, setDashboardData] = useState(null);
  const [statusData, setStatusData] = useState(null);
  const [activeTab, setActiveTab] = useState('active');
  const [loading, setLoading] = useState(true);
  const [sendingNow, setSendingNow] = useState(false);
  const [sendMsg, setSendMsg] = useState('');

  const fetchDashboard = async () => {
    try {
      const data = await api.getSequenceDashboard(sequenceId);
      if (data && data.success) {
        setDashboardData(data);
      }
      const st = await api.getStatus().catch(() => null);
      if (st) setStatusData(st);
      return true;
    } catch (err) {
      if (err.message && (err.message.includes('401') || err.message.includes('Unauthorized'))) {
        return false; // Stop polling on 401
      }
      console.error('Dashboard fetch error:', err.message || err);
      return true;
    } finally {
      setLoading(false);
    }
  };

  const handleManualSend = async () => {
    setSendingNow(true);
    setSendMsg('');
    try {
      const res = await api.triggerSend();
      if (res.success && res.processed) {
        const p = res.processed;
        setSendMsg(`Send pass complete: ${p.sent || 0} sent, ${p.skipped || 0} skipped, ${p.failed || 0} failed.`);
      } else {
        setSendMsg('Send trigger complete.');
      }
      await fetchDashboard();
    } catch (err) {
      setSendMsg('Trigger error: ' + err.message);
    } finally {
      setSendingNow(false);
    }
  };

  useEffect(() => {
    let mounted = true;
    let timerId = null;

    const startPolling = async () => {
      const success = await fetchDashboard();
      if (success && mounted) {
        timerId = setInterval(async () => {
          const ok = await fetchDashboard();
          if (!ok && timerId) {
            clearInterval(timerId);
          }
        }, 5000);
      }
    };

    startPolling();

    return () => {
      mounted = false;
      if (timerId) clearInterval(timerId);
    };
  }, [sequenceId]);

  const hasSequence = dashboardData?.sequence != null;
  const seq = dashboardData?.sequence;
  const stats = dashboardData?.stats || {};
  const lists = dashboardData?.lists || {};
  const logs = dashboardData?.logs || statusData?.logs || [];
  const scheduleInfo = dashboardData?.schedule_info;

  const recipientRow = (rec, idx) => (
    <div key={idx} className="recipient-item" style={{ padding: '0.75rem', borderBottom: '1px solid var(--border-color, #333)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
      <div>
        <div style={{ fontWeight: 600 }}>{rec.email}</div>
        <div style={{ fontSize: '0.8rem', color: 'var(--text-muted, #888)' }}>
          {rec.step_label || `Step ${rec.current_step + 1}`}
          {rec.error ? <span style={{ color: 'var(--danger, #ef4444)', marginLeft: 8 }}>⚠️ {rec.error}</span> : null}
        </div>
      </div>
      <div style={{ textAlign: 'right', fontSize: '0.8rem', color: 'var(--text-muted, #888)' }}>
        {rec.next_send_at ? (
          <span>⏰ Next: {new Date(rec.next_send_at).toLocaleString()}</span>
        ) : rec.last_sent_at ? (
          <span>✅ Last Sent: {new Date(rec.last_sent_at).toLocaleString()}</span>
        ) : (
          <span style={{ textTransform: 'capitalize' }}>{rec.status || 'Pending'}</span>
        )}
      </div>
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
          {/* SCHEDULE STATUS BANNER */}
          {scheduleInfo && (
            <div className="card" style={{
              background: scheduleInfo.is_in_window ? 'rgba(16, 185, 129, 0.08)' : 'rgba(245, 158, 11, 0.08)',
              borderLeft: `4px solid ${scheduleInfo.is_in_window ? '#10b981' : '#f59e0b'}`,
              display: 'flex',
              justify: 'space-between',
              alignItems: 'center',
              flexWrap: 'wrap',
              gap: '1rem'
            }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontWeight: 600, fontSize: '0.95rem' }}>
                  <Clock size={18} style={{ color: scheduleInfo.is_in_window ? '#10b981' : '#f59e0b' }} />
                  <span>{scheduleInfo.status_message}</span>
                </div>
                {sendMsg && <div style={{ fontSize: '0.85rem', color: '#10b981', marginTop: '0.25rem' }}>{sendMsg}</div>}
              </div>

              <button
                className="btn-primary"
                onClick={handleManualSend}
                disabled={sendingNow}
                style={{ width: 'auto', padding: '0.4rem 0.9rem', fontSize: '0.85rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}
              >
                {sendingNow ? <RefreshCw size={14} className="spin" /> : <Play size={14} />}
                {sendingNow ? 'Sending Emails...' : 'Send Due Emails Now'}
              </button>
            </div>
          )}
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
