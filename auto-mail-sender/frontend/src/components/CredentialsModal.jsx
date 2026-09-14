import React, { useState } from 'react';
import { KeyRound, X, Save, CheckCircle2 } from 'lucide-react';
import { api } from '../api';

export const CredentialsModal = ({ isOpen, onClose, onSuccess, showToast }) => {
  const [provider, setProvider] = useState('smtp'); // 'smtp' | 'brevo'
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [brevoApiKey, setBrevoApiKey] = useState('');
  const [brevoSenderEmail, setBrevoSenderEmail] = useState('');
  const [saving, setSaving] = useState(false);

  if (!isOpen) return null;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (provider === 'smtp' && (!email || !password)) {
      showToast('Both Sender Gmail and App Password are required.', 'error');
      return;
    }
    if (provider === 'brevo' && (!brevoApiKey || !brevoSenderEmail)) {
      showToast('Both Brevo API Key and Sender Email are required.', 'error');
      return;
    }

    setSaving(true);
    try {
      const payload = {
        email_provider: provider,
        smtp_email: email || brevoSenderEmail,
        smtp_app_password: password,
        brevo_api_key: brevoApiKey,
        brevo_sender_email: brevoSenderEmail || email,
      };
      const res = await api.saveSettings(payload);
      if (res.success) {
        showToast('Credentials saved successfully!', 'success');
        onClose();
        if (onSuccess) onSuccess();
      } else {
        showToast(res.error || 'Failed to save credentials', 'error');
      }
    } catch (err) {
      showToast(err.message || 'Failed to save credentials', 'error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-backdrop active">
      <div className="modal-dialog" style={{ maxWidth: '480px' }}>
        <div className="modal-header">
          <h3>
            <KeyRound size={20} style={{ display: 'inline', marginRight: 8 }} /> Configure Email Sender
          </h3>
          <button type="button" className="modal-close-btn" onClick={onClose}>
            <X size={18} />
          </button>
        </div>
        <div className="modal-body">
          <p style={{ fontSize: '0.875rem', color: 'var(--text-muted)', marginBottom: '1rem' }}>
            Choose your sending method to enable automated outreach:
          </p>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginBottom: '1.25rem' }}>
            <button
              type="button"
              onClick={() => setProvider('smtp')}
              style={{
                padding: '8px 12px',
                borderRadius: '6px',
                border: provider === 'smtp' ? '2px solid var(--primary)' : '1px solid var(--border)',
                background: provider === 'smtp' ? 'rgba(79, 70, 229, 0.08)' : 'transparent',
                cursor: 'pointer',
                fontWeight: 600,
                fontSize: '0.85rem',
                color: provider === 'smtp' ? 'var(--primary)' : 'inherit',
              }}
            >
              Gmail SMTP (SSL)
            </button>
            <button
              type="button"
              onClick={() => setProvider('brevo')}
              style={{
                padding: '8px 12px',
                borderRadius: '6px',
                border: provider === 'brevo' ? '2px solid var(--primary)' : '1px solid var(--border)',
                background: provider === 'brevo' ? 'rgba(79, 70, 229, 0.08)' : 'transparent',
                cursor: 'pointer',
                fontWeight: 600,
                fontSize: '0.85rem',
                color: provider === 'brevo' ? 'var(--primary)' : 'inherit',
              }}
            >
              Brevo API (HTTPS)
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
                    placeholder="name@gmail.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                  />
                </div>
                <div className="form-group" style={{ marginBottom: '1.25rem' }}>
                  <label style={{ fontWeight: 600 }}>Gmail App Password</label>
                  <input
                    type="password"
                    required
                    placeholder="xxxx xxxx xxxx xxxx"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
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
                </div>
                <div className="form-group" style={{ marginBottom: '1.25rem' }}>
                  <label style={{ fontWeight: 600 }}>Brevo Verified Sender Email</label>
                  <input
                    type="email"
                    required
                    placeholder="sender@yourdomain.com"
                    value={brevoSenderEmail}
                    onChange={(e) => setBrevoSenderEmail(e.target.value)}
                  />
                </div>
              </>
            )}

            <button type="submit" className="btn-primary" disabled={saving} style={{ width: '100%' }}>
              <Save size={16} style={{ marginRight: 6 }} />
              {saving ? 'Saving...' : 'Save & Continue Launch'}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
};
