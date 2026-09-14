import React, { useState, useEffect } from 'react';
import { User, Mail, Save, Send, CheckCircle2, AlertCircle, ShieldCheck, Zap } from 'lucide-react';
import { api } from '../api';

export const SettingsView = ({ showToast }) => {
  const [profile, setProfile] = useState(null);
  const [provider, setProvider] = useState('smtp'); // 'smtp' | 'brevo'

  // SMTP Gmail
  const [smtpEmail, setSmtpEmail] = useState('');
  const [smtpPassword, setSmtpPassword] = useState('');
  const [imapHost, setImapHost] = useState('imap.gmail.com');
  const [imapPort, setImapPort] = useState(993);

  // Brevo API
  const [brevoApiKey, setBrevoApiKey] = useState('');
  const [brevoSenderEmail, setBrevoSenderEmail] = useState('');
  const [brevoSenderName, setBrevoSenderName] = useState('');

  // Test Email
  const [testRecipient, setTestRecipient] = useState('');
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);

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
        setProvider(res.email_provider || 'smtp');
        setSmtpEmail(res.smtp_email || '');
        setSmtpPassword(res.smtp_app_password || '');
        setBrevoApiKey(res.brevo_api_key || '');
        setBrevoSenderEmail(res.brevo_sender_email || res.smtp_email || '');
        setBrevoSenderName(res.brevo_sender_name || '');
        setImapHost(res.imap_host || 'imap.gmail.com');
        setImapPort(res.imap_port || 993);

        if (!testRecipient) {
          setTestRecipient(res.smtp_email || res.brevo_sender_email || '');
        }
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

    if (provider === 'smtp' && !smtpEmail) {
      showToast('Sender Gmail is required.', 'error');
      return;
    }
    if (provider === 'brevo' && !brevoSenderEmail) {
      showToast('Brevo sender email is required.', 'error');
      return;
    }

    setSaving(true);
    setTestResult(null);
    try {
      const emailValue = (provider === 'brevo' ? brevoSenderEmail : smtpEmail) || brevoSenderEmail || smtpEmail;
      const fd = new FormData();
      fd.append('email_provider', provider);
      fd.append('smtp_email', emailValue);
      fd.append('brevo_sender_email', emailValue);
      if (smtpPassword) fd.append('smtp_app_password', smtpPassword);
      if (brevoApiKey) fd.append('brevo_api_key', brevoApiKey);
      if (brevoSenderName) fd.append('brevo_sender_name', brevoSenderName);
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

  const handleTestEmail = async () => {
    if (!testRecipient) {
      showToast('Please enter a recipient email for testing.', 'error');
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const res = await api.testEmailConnection({
        email_provider: provider,
        test_recipient: testRecipient,
      });
      if (res.success) {
        setTestResult({ success: true, message: res.message });
        showToast(res.message, 'success');
      } else {
        setTestResult({ success: false, message: res.error || 'Test email failed.' });
        showToast(res.error || 'Test email failed', 'error');
      }
    } catch (err) {
      const msg = err.message || 'Connection test failed';
      setTestResult({ success: false, message: msg });
      showToast('Test failed: ' + msg, 'error');
    } finally {
      setTesting(false);
    }
  };

  if (loading) {
    return <div className="card" style={{ padding: '2rem', textAlign: 'center' }}>Loading settings...</div>;
  }

  return (
    <section id="view-settings" className="view-pane">
      <div className="setup-container" style={{ maxWidth: '640px', margin: '0 auto' }}>
        {/* USER PROFILE */}
        <div className="card" style={{ marginBottom: '1.5rem' }}>
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

        {/* EMAIL PROVIDER & CREDENTIALS */}
        <div className="card" style={{ marginBottom: '1.5rem' }}>
          <h2>
            <Mail size={20} style={{ display: 'inline', marginRight: 8 }} /> Email Sending Configuration
          </h2>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem', marginBottom: '1.25rem' }}>
            Select how you want your outreach emails to be delivered.
          </p>

          {/* Provider Selection Tabs */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', marginBottom: '1.5rem' }}>
            <button
              type="button"
              onClick={() => setProvider('smtp')}
              style={{
                padding: '12px',
                borderRadius: '8px',
                border: provider === 'smtp' ? '2px solid var(--primary)' : '1px solid var(--border)',
                background: provider === 'smtp' ? 'rgba(79, 70, 229, 0.08)' : 'var(--card-bg, #fff)',
                cursor: 'pointer',
                textAlign: 'left',
                display: 'flex',
                flexDirection: 'column',
                gap: '4px',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <span style={{ fontWeight: 700, fontSize: '0.95rem', color: provider === 'smtp' ? 'var(--primary)' : 'inherit' }}>
                  Gmail SMTP (SSL)
                </span>
                {provider === 'smtp' && <CheckCircle2 size={16} color="var(--primary)" />}
              </div>
              <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                Port 465 SSL Direct
              </span>
            </button>

            <button
              type="button"
              onClick={() => setProvider('brevo')}
              style={{
                padding: '12px',
                borderRadius: '8px',
                border: provider === 'brevo' ? '2px solid var(--primary)' : '1px solid var(--border)',
                background: provider === 'brevo' ? 'rgba(79, 70, 229, 0.08)' : 'var(--card-bg, #fff)',
                cursor: 'pointer',
                textAlign: 'left',
                display: 'flex',
                flexDirection: 'column',
                gap: '4px',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <span style={{ fontWeight: 700, fontSize: '0.95rem', color: provider === 'brevo' ? 'var(--primary)' : 'inherit' }}>
                  Brevo REST API
                </span>
                {provider === 'brevo' && <CheckCircle2 size={16} color="var(--primary)" />}
              </div>
              <span style={{ fontSize: '0.78rem', color: '#10b981', fontWeight: 600 }}>
                HTTPS 443 (Zero Port Issues)
              </span>
            </button>
          </div>

          <form onSubmit={handleSubmit}>
            {provider === 'smtp' ? (
              <>
                <div className="form-group" style={{ marginBottom: '1rem' }}>
                  <label style={{ fontWeight: 600 }}>Sender Gmail Address</label>
                  <input
                    type="email"
                    required
                    placeholder="yourname@gmail.com"
                    value={smtpEmail}
                    onChange={(e) => setSmtpEmail(e.target.value)}
                  />
                </div>
                <div className="form-group" style={{ marginBottom: '1.25rem' }}>
                  <label style={{ fontWeight: 600 }}>Gmail App Password</label>
                  <input
                    type="password"
                    required
                    placeholder="xxxx xxxx xxxx xxxx"
                    value={smtpPassword}
                    onChange={(e) => setSmtpPassword(e.target.value)}
                  />
                  <small style={{ color: 'var(--text-muted)', fontSize: '0.75rem', marginTop: '4px', display: 'block' }}>
                    Generate an App Password from Google Account &gt; Security &gt; 2-Step Verification &gt; App Passwords.
                  </small>
                </div>
              </>
            ) : (
              <>
                <div className="form-group" style={{ marginBottom: '1rem' }}>
                  <label style={{ fontWeight: 600 }}>Brevo API Key (v3)</label>
                  <input
                    type="password"
                    required
                    placeholder="xkeysib-..."
                    value={brevoApiKey}
                    onChange={(e) => setBrevoApiKey(e.target.value)}
                  />
                  <small style={{ color: 'var(--text-muted)', fontSize: '0.75rem', marginTop: '4px', display: 'block' }}>
                    Find your API key in Brevo &gt; SMTP &amp; API &gt; API Keys tab.
                  </small>
                </div>
                <div className="form-group" style={{ marginBottom: '1rem' }}>
                  <label style={{ fontWeight: 600 }}>Brevo Verified Sender Email</label>
                  <input
                    type="email"
                    required
                    placeholder="verified-sender@yourdomain.com"
                    value={brevoSenderEmail}
                    onChange={(e) => setBrevoSenderEmail(e.target.value)}
                  />
                  <small style={{ color: 'var(--text-muted)', fontSize: '0.75rem', marginTop: '4px', display: 'block' }}>
                    Must be added and verified in Brevo &gt; Senders &amp; IP.
                  </small>
                </div>
                <div className="form-group" style={{ marginBottom: '1.25rem' }}>
                  <label style={{ fontWeight: 600 }}>Sender Name (Optional)</label>
                  <input
                    type="text"
                    placeholder="e.g. Kuldeep | Support"
                    value={brevoSenderName}
                    onChange={(e) => setBrevoSenderName(e.target.value)}
                  />
                </div>
              </>
            )}

            <button
              type="submit"
              className="btn-primary"
              disabled={saving}
              style={{ width: '100%', padding: '12px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px' }}
            >
              <Save size={18} />
              {saving ? 'Saving Settings...' : 'Save Configuration'}
            </button>
          </form>
        </div>

        {/* TEST CONNECTION CARD */}
        <div className="card">
          <h2>
            <Zap size={20} style={{ display: 'inline', marginRight: 8, color: '#f59e0b' }} /> Test Email Connection
          </h2>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem', marginBottom: '1rem' }}>
            Send a live test email right now to verify that your selected provider ({provider === 'smtp' ? 'Gmail SMTP' : 'Brevo API'}) is delivering properly.
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginBottom: '1rem' }}>
            <div className="form-group" style={{ marginBottom: 0 }}>
              <label style={{ fontWeight: 600, fontSize: '0.85rem' }}>Recipient Email (Send test mail to)</label>
              <input
                type="email"
                placeholder="e.g. your-email@gmail.com"
                value={testRecipient}
                onChange={(e) => setTestRecipient(e.target.value)}
                style={{ width: '100%', marginTop: '4px' }}
              />
            </div>
            <button
              type="button"
              className="btn-secondary"
              onClick={handleTestEmail}
              disabled={testing || !testRecipient}
              style={{ padding: '10px 16px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px', width: '100%', fontWeight: 600 }}
            >
              <Send size={16} />
              {testing ? 'Sending Test Email...' : 'Send Test Email Now'}
            </button>
          </div>

          {testResult && (
            <div
              style={{
                padding: '12px',
                borderRadius: '6px',
                display: 'flex',
                alignItems: 'flex-start',
                gap: '8px',
                fontSize: '0.875rem',
                backgroundColor: testResult.success ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)',
                color: testResult.success ? '#065f46' : '#991b1b',
                border: `1px solid ${testResult.success ? '#10b981' : '#ef4444'}`,
              }}
            >
              {testResult.success ? <CheckCircle2 size={18} /> : <AlertCircle size={18} />}
              <div>{testResult.message}</div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
};
