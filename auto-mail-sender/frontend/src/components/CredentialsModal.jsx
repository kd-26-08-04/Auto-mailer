import React, { useState } from 'react';
import { KeyRound, X, Save } from 'lucide-react';
import { api } from '../api';

export const CredentialsModal = ({ isOpen, onClose, onSuccess, showToast }) => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [saving, setSaving] = useState(false);

  if (!isOpen) return null;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!email || !password) {
      showToast('Both fields are required.', 'error');
      return;
    }
    setSaving(true);
    try {
      const fd = new FormData();
      fd.append('smtp_email', email);
      fd.append('smtp_app_password', password);
      const res = await api.saveSettings(fd);
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
            <KeyRound size={20} style={{ display: 'inline', marginRight: 8 }} /> Gmail App Password Required
          </h3>
          <button type="button" className="modal-close-btn" onClick={onClose}>
            <X size={18} />
          </button>
        </div>
        <div className="modal-body">
          <p style={{ fontSize: '0.875rem', color: 'var(--text-muted)', marginBottom: '1.25rem' }}>
            Please enter your Sender Gmail address and Gmail App Password to enable sequence outreach.
          </p>
          <form onSubmit={handleSubmit}>
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
