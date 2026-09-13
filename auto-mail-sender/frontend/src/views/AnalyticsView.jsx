import React, { useState, useEffect } from 'react';
import { Filter, Calendar, Layers } from 'lucide-react';
import { api } from '../api';

export const AnalyticsView = ({ sequenceId }) => {
  const [analyticsData, setAnalyticsData] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchAnalytics = async () => {
    try {
      const res = await api.getAnalytics(sequenceId);
      if (res.success) {
        setAnalyticsData(res);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAnalytics();
  }, [sequenceId]);

  if (loading) {
    return <div className="card" style={{ padding: '2rem', textAlign: 'center' }}>Loading analytics...</div>;
  }

  if (!analyticsData || !analyticsData.sequence) {
    return (
      <section id="view-analytics" className="view-pane">
        <div className="card" style={{ textAlign: 'center', padding: '2rem' }}>
          <p style={{ color: 'var(--text-muted)' }}>
            No data yet. Launch a sequence to see analytics.
          </p>
        </div>
      </section>
    );
  }

  const seq = analyticsData.sequence;
  const summary = analyticsData.summary || {};
  const funnel = analyticsData.funnel || [];
  const daily = analyticsData.daily || [];
  const steps = analyticsData.steps || [];

  const maxFunnelCount = Math.max(...funnel.map((f) => f.count), 1);
  const maxDailySent = Math.max(...daily.map((d) => d.sent), 1);

  return (
    <section id="view-analytics" className="view-pane">
      <div className="content-stack">
        <p style={{ fontWeight: 600, fontSize: '1.1rem', color: 'var(--text)' }}>
          {seq.name}
        </p>

        {/* METRICS GRID */}
        <div className="stats-grid">
          <div className="card stat-card">
            <div className="stat-label">Open Rate</div>
            <div className="stat-value" style={{ color: 'var(--success)' }}>
              {summary.open_rate || 0}%
            </div>
          </div>
          <div className="card stat-card">
            <div className="stat-label">Click Rate</div>
            <div className="stat-value" style={{ color: 'var(--info)' }}>
              {summary.click_rate || 0}%
            </div>
          </div>
          <div className="card stat-card">
            <div className="stat-label">Reply Rate</div>
            <div className="stat-value" style={{ color: '#8b5cf6' }}>
              {summary.reply_rate || 0}%
            </div>
          </div>
          <div className="card stat-card">
            <div className="stat-label">Emails Sent</div>
            <div className="stat-value">{summary.total_emails_sent || 0}</div>
          </div>
        </div>

        {/* FUNNEL & DAILY SENDS */}
        <div className="grid-2" style={{ gap: '1.75rem' }}>
          <div className="card">
            <h2>
              <Filter size={20} style={{ display: 'inline', marginRight: 8 }} /> Funnel
            </h2>
            <div className="funnel-chart">
              {funnel.map((f, idx) => (
                <div key={idx} className="funnel-row">
                  <span className="funnel-label">{f.label}</span>
                  <div className="funnel-bar-bg">
                    <div
                      className="funnel-bar-fill"
                      style={{ width: `${Math.round((f.count / maxFunnelCount) * 100)}%` }}
                    />
                  </div>
                  <span className="funnel-count">{f.count}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="card">
            <h2>
              <Calendar size={20} style={{ display: 'inline', marginRight: 8 }} /> Sends (last 14 days)
            </h2>
            <div className="bar-chart">
              {daily.length === 0 ? (
                <p style={{ color: 'var(--text-muted)', padding: '1rem' }}>No sends yet</p>
              ) : (
                daily.map((d, idx) => (
                  <div key={idx} className="bar-row">
                    <span className="bar-label">{d.day.slice(5)}</span>
                    <div className="bar-bg">
                      <div
                        className="bar-fill"
                        style={{ width: `${Math.round((d.sent / maxDailySent) * 100)}%` }}
                      />
                    </div>
                    <span className="bar-count">{d.sent}</span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

        {/* STEP ANALYTICS TABLE */}
        <div className="card">
          <h2>
            <Layers size={20} style={{ display: 'inline', marginRight: 8 }} /> Performance by Step
          </h2>
          <div className="step-analytics-table">
            {steps.length === 0 ? (
              <p style={{ color: 'var(--text-muted)', padding: '1rem' }}>No step data</p>
            ) : (
              <table className="analytics-table">
                <thead>
                  <tr>
                    <th>Step</th>
                    <th>Sent</th>
                    <th>Opened</th>
                    <th>Open %</th>
                    <th>Clicked</th>
                    <th>Click %</th>
                  </tr>
                </thead>
                <tbody>
                  {steps.map((st, idx) => (
                    <tr key={idx}>
                      <td>Step {st.step}</td>
                      <td>{st.sent}</td>
                      <td>{st.opened}</td>
                      <td>{st.open_rate}%</td>
                      <td>{st.clicked}</td>
                      <td>{st.click_rate}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
    </section>
  );
};
