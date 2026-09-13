import React, { useState, useEffect } from 'react';
import { User, Mail, Save } from 'lucide-react';
import { api } from '../api';

export const SettingsView = ({ showToast }) => {
  const [profile, setProfile] = useState(null);
  const [smtpEmail, setSmtpEmail] = useState('');
  const [smtpPassword, setSmtpPassword] = useState('');
  const [imapHost, setImapHost] = useState('imap.gmail.com');
  const [imapPort, setImapPort] = useState(993);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const fetchSettings = async () => {
    try {
      const res = await api.getSettings();
      if (res.success) {
        setProfile({
          full_name: res.full_name,
          phone: res.phone,
          username: res.username,
        });
        setSmtpEmail(res.smtp_email || '');
        setSmtpPassword(res.smtp_app_password || '');
        setImapHost(res.imap_host || 'imap.gmail.com');
        setImapPort(res.imap_port || 993);
      }
    } catch (e) {
      showToast('Failed to load settings', 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSettings();
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!smtpEmail) {
      showToast('Sender email is required.', 'error');
      return;
    }
    setSaving(true);
    try {
      const fd = new FormData();
      fd.append('smtp_email', smtpEmail);
      if (smtpPassword) fd.append('smtp_app_password', smtpPassword);
      fd.append('imap_host', imapHost);
      fd.append('imap_port', imapPort);

      const res = await api.saveSettings(fd);
      if (res.success) {
        showToast('Settings saved successfully.', 'success');
        fetchSettings();
      } else {
        showToast(res.error || 'Failed to save settings', 'error');
      }
    } catch (err) {
      showToast('Failed to save settings: ' + err.message, 'error');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="card" style={{ padding: '2rem', textAlign: 'center' }}>Loading settings...</div>;
  }

  return (
    <section id="view-settings" className="view-pane">
      <div className="setup-container" style={{ maxWidth: '600px', margin: '0 auto' }}>
        {/* USER PROFILE */}
        <div className="card" style={{ marginBottom: '2rem' }}>
          <h2>
            <User size={20} style={{ display: 'inline', marginRight: 8 }} /> User Profile
          </h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', marginTop: '1.5rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--border)', paddingBottom: '0.75rem' }}>
              <span style={{ fontWeight: 600 }}>Full Name</span>
              <span style={{ color: 'var(--text-muted)' }}>{profile?.full_name || 'N/A'}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--border)', paddingBottom: '0.75rem' }}>
              <span style={{ fontWeight: 600 }}>Phone</span>
              <span style={{ color: 'var(--text-muted)' }}>{profile?.phone || 'N/A'}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', paddingBottom: '0.75rem' }}>
              <span style={{ fontWeight: 600 }}>Username</span>
              <span style={{ color: 'var(--text-muted)' }}>{profile?.username || 'N/A'}</span>
            </div>
          </div>
        </div>

        {/* EMAIL CREDENTIALS */}
        <div className="card">
          <h2>
            <Mail size={20} style={{ display: 'inline', marginRight: 8 }} /> Email Credentials
          </h2>
          <form onSubmit={handleSubmit}>
            <div className="form-group">
              <label>Sender Gmail</label>
              <input
                type="email"
                required
                value={smtpEmail}
                onChange={(e) => setSmtpEmail(e.target.value)}
              />
            </div>
            <div className="form-group">
              <label>Gmail App Password</label>
              <input
                type="password"
                required
                placeholder="********"
                value={smtpPassword}
                onChange={(e) => setSmtpPassword(e.target.value)}
              />
            </div>
            <button type="submit" className="btn-primary" disabled={saving} style={{ marginTop: '1rem', width: '100%' }}>
              <Save size={16} style={{ marginRight: 6 }} />
              {saving ? 'Saving...' : 'Save'}
            </button>
          </form>
        </div>
      </div>
    </section>
  );
};
