import React from 'react';
import { Table, X, UserPlus, Trash2, Rocket } from 'lucide-react';

export const LeadReviewModal = ({
  isOpen,
  onClose,
  leads = [],
  setLeads,
  onConfirmStart,
  starting = false,
}) => {
  if (!isOpen) return null;

  // Determine key columns to display
  const keys = ['email', 'first_name', 'company', 'consent'];
  leads.forEach((row) => {
    Object.keys(row).forEach((k) => {
      if (!keys.includes(k.toLowerCase()) && k !== '_id') {
        keys.push(k.toLowerCase());
      }
    });
  });

  const handleCellChange = (index, key, value) => {
    const updated = [...leads];
    updated[index] = { ...updated[index], [key]: value.trim() };
    setLeads(updated);
  };

  const handleAddRow = () => {
    setLeads([...leads, { email: '', first_name: '', company: '', consent: 'true' }]);
  };

  const handleRemoveRow = (index) => {
    const updated = leads.filter((_, i) => i !== index);
    setLeads(updated);
  };

  return (
    <div className="modal-backdrop active">
      <div className="modal-dialog">
        <div className="modal-header">
          <h3>
            <Table size={20} style={{ display: 'inline', marginRight: 8 }} /> Review & Edit Contacts
          </h3>
          <button type="button" className="modal-close-btn" onClick={onClose}>
            <X size={18} />
          </button>
        </div>
        <div className="modal-body">
          <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '1rem' }}>
            Parsed contacts from file. Modify any cell, add or remove lead rows before launching sequence.
          </p>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
            <span className="badge badge-idle">{leads.length} contacts</span>
            <button
              type="button"
              className="btn-secondary"
              style={{ width: 'auto', fontSize: '0.8rem', padding: '0.4rem 0.75rem' }}
              onClick={handleAddRow}
            >
              <UserPlus size={14} style={{ marginRight: 4 }} /> Add Row
            </button>
          </div>
          <div className="lead-table-wrapper">
            <table className="lead-table">
              <thead>
                <tr>
                  <th>#</th>
                  {keys.map((k) => (
                    <th key={k}>{k}</th>
                  ))}
                  <th style={{ textAlign: 'center' }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {leads.length === 0 ? (
                  <tr>
                    <td colSpan={keys.length + 2} style={{ padding: '1.5rem', textAlign: 'center', color: 'var(--text-muted)' }}>
                      No contacts in list. Click "Add Row" to add a lead manually.
                    </td>
                  </tr>
                ) : (
                  leads.map((row, idx) => (
                    <tr key={idx}>
                      <td style={{ fontWeight: 600, color: 'var(--text-muted)' }}>{idx + 1}</td>
                      {keys.map((k) => {
                        const val = row[k] ?? row[k.toLowerCase()] ?? '';
                        return (
                          <td key={k}>
                            <input
                              type="text"
                              value={val}
                              onChange={(e) => handleCellChange(idx, k, e.target.value)}
                            />
                          </td>
                        );
                      })}
                      <td style={{ textAlign: 'center' }}>
                        <button
                          type="button"
                          className="btn-icon btn-icon-sm"
                          onClick={() => handleRemoveRow(idx)}
                          title="Delete lead"
                        >
                          <Trash2 size={14} />
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
        <div className="modal-footer">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={starting}
            onClick={onConfirmStart}
          >
            <Rocket size={16} style={{ marginRight: 6 }} />
            {starting ? 'Launching...' : 'Confirm & Start Sequence'}
          </button>
        </div>
      </div>
    </div>
  );
};
