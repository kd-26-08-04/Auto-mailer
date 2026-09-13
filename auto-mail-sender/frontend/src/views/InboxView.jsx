import React, { useState, useEffect } from 'react';
import { api } from '../api';

export const InboxView = ({ sequenceId }) => {
  const [inboxData, setInboxData] = useState(null);
  const [activeTab, setActiveTab] = useState('opened');
  const [loading, setLoading] = useState(true);

  const fetchInbox = async () => {
    try {
      const res = await api.getInbox(sequenceId);
      if (res.success) {
        setInboxData(res);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchInbox();
  }, [sequenceId]);

  if (loading) {
    return <div className="card" style={{ padding: '2rem', textAlign: 'center' }}>Loading inbox...</div>;
  }

  if (!inboxData || !inboxData.sequence) {
    return (
      <section id="view-inbox" className="view-pane">
        <div className="card" style={{ textAlign: 'center', padding: '2rem' }}>
          <p style={{ color: 'var(--text-muted)' }}>
            No sequence activity yet. Launch a sequence to track opens and replies here.
          </p>
        </div>
      </section>
    );
  }

  const seq = inboxData.sequence;
  const groups = inboxData.groups || {};
  const groupLists = inboxData.group_lists || {};

  const contactRow = (c, idx) => {
    let badges = [];
    if (c.opened) badges.push(<span key="open" className="mini-badge mini-open">Opened</span>);
    if (c.clicked > 0) badges.push(<span key="click" className="mini-badge mini-click">Clicked {c.clicked}</span>);
    if (c.replied) badges.push(<span key="reply" className="mini-badge mini-reply">Replied</span>);
    if (c.emails_sent > 0 && !c.opened) badges.push(<span key="no-open" className="mini-badge mini-no-open">Not opened</span>);

    const meta = c.emails_sent > 0
      ? `Sent ${c.emails_sent} · Step ${c.last_step_sent || c.current_step}${c.last_sent_at ? ' · ' + new Date(c.last_sent_at).toLocaleString() : ''}`
      : (c.next_send_at ? 'Scheduled · ' + new Date(c.next_send_at).toLocaleString() : 'Waiting to send');

    return (
      <div key={idx} className="recipient-item">
        <span>
          <span className="status-dot"></span>
          {c.email} {badges}
        </span>
        <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>{meta}</span>
      </div>
    );
  };

  return (
    <section id="view-inbox" className="view-pane">
      <div className="content-stack">
        <div className="card">
          <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
            Activity for contacts <strong>you emailed</strong> only — not your full Gmail inbox.
          </p>
          <p style={{ fontWeight: 600, marginTop: '0.5rem' }}>{seq.name}</p>
        </div>

        <div className="stats-grid inbox-stats">
          <div className="card stat-card">
            <div className="stat-label">Opened</div>
            <div className="stat-value" style={{ color: 'var(--success)' }}>
              {groups.opened || 0}
            </div>
          </div>
          <div className="card stat-card">
            <div className="stat-label">Not Opened</div>
            <div className="stat-value" style={{ color: 'var(--warning)' }}>
              {groups.not_opened || 0}
            </div>
          </div>
          <div className="card stat-card">
            <div className="stat-label">Replied</div>
            <div className="stat-value" style={{ color: '#8b5cf6' }}>
              {groups.replied || 0}
            </div>
          </div>
          <div className="card stat-card">
            <div className="stat-label">No Response</div>
            <div className="stat-value">{groups.no_response || 0}</div>
          </div>
        </div>

        <div className="card">
          <div className="tabs">
            <div className={`tab ${activeTab === 'opened' ? 'active' : ''}`} onClick={() => setActiveTab('opened')}>
              Opened 👁️ ({groups.opened || 0})
            </div>
            <div className={`tab ${activeTab === 'not_opened' ? 'active' : ''}`} onClick={() => setActiveTab('not_opened')}>
              Not Opened ({groups.not_opened || 0})
            </div>
            <div className={`tab ${activeTab === 'replied' ? 'active' : ''}`} onClick={() => setActiveTab('replied')}>
              Replied 💬 ({groups.replied || 0})
            </div>
            <div className={`tab ${activeTab === 'no_response' ? 'active' : ''}`} onClick={() => setActiveTab('no_response')}>
              No Response ({groups.no_response || 0})
            </div>
            <div className={`tab ${activeTab === 'pending' ? 'active' : ''}`} onClick={() => setActiveTab('pending')}>
              Not Sent Yet ({groupLists.pending?.length || 0})
            </div>
          </div>

          <div className="tab-content recipient-list" style={{ display: 'block' }}>
            {(groupLists[activeTab] || []).length === 0 ? (
              <p style={{ padding: '1rem', color: 'var(--text-muted)' }}>None in this category</p>
            ) : (
              (groupLists[activeTab] || []).map(contactRow)
            )}
          </div>
        </div>
      </div>
    </section>
  );
};
