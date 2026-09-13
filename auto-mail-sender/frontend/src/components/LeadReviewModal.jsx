import React, { useEffect, useRef, useState } from 'react';
import { Table, X, UserPlus, Trash2, Rocket, Save, Upload, Keyboard } from 'lucide-react';
import { api } from '../api';

/**
 * Add People modal: Upload file OR enter details manually.
 * Actions: Save contacts (enroll / keep draft) or Start sequence.
 */
export const LeadReviewModal = ({
  isOpen,
  onClose,
  leads = [],
  setLeads,
  onSaveContacts,
  onConfirmStart,
  starting = false,
  saving = false,
  mode = 'launch', // 'launch' | 'enroll'
}) => {
  const fileRef = useRef(null);
  const [entryMode, setEntryMode] = useState('choose');
  const [parsing, setParsing] = useState(false);
  const [parseError, setParseError] = useState('');

  useEffect(() => {
    if (isOpen) {
      setEntryMode(leads.length > 0 ? 'review' : 'choose');
      setParseError('');
    }
  }, [isOpen]);

  if (!isOpen) return null;

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
    setEntryMode('review');
  };

  const handleRemoveRow = (index) => {
    setLeads(leads.filter((_, i) => i !== index));
  };

  const startManualEntry = () => {
    setParseError('');
    if (leads.length === 0) {
      setLeads([{ email: '', first_name: '', company: '', consent: 'true' }]);
    }
    setEntryMode('review');
  };

  const handleFilePick = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setParsing(true);
    setParseError('');
    const fd = new FormData();
    fd.append('recipients_file', file);
    try {
      const res = await api.parseLeads(fd);
      if (res.success && res.rows?.length) {
        setLeads(res.rows);
        setEntryMode('review');
      } else {
        setParseError(res.error || 'Failed to parse file');
      }
    } catch (err) {
      setParseError(err.message || 'Failed to parse file');
    } finally {
      setParsing(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const validCount = leads.filter((r) => String(r.email || '').includes('@')).length;
  const primaryLabel = mode === 'enroll' ? 'Add to Sequence' : 'Start Sequence';
  const primaryIcon = mode === 'enroll' ? <UserPlus size={16} style={{ marginRight: 6 }} /> : <Rocket size={16} style={{ marginRight: 6 }} />;

  return (
    <div className="modal-backdrop active">
      <div className="modal-dialog" style={{ maxWidth: 720 }}>
        <div className="modal-header">
          <h3>
            <Table size={20} style={{ display: 'inline', marginRight: 8 }} /> Add People
          </h3>
          <button type="button" className="modal-close-btn" onClick={onClose}>
            <X size={18} />
          </button>
        </div>
        <div className="modal-body">
          {(entryMode === 'choose' || entryMode === 'review') && (
            <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
              <button
                type="button"
                className={`btn-secondary ${entryMode === 'choose' ? '' : ''}`}
                style={{ width: 'auto', fontSize: '0.85rem' }}
                onClick={() => fileRef.current?.click()}
                disabled={parsing}
              >
                <Upload size={14} style={{ marginRight: 6 }} />
                {parsing ? 'Reading file…' : 'Upload file'}
              </button>
              <button
                type="button"
                className="btn-secondary"
                style={{ width: 'auto', fontSize: '0.85rem' }}
                onClick={startManualEntry}
              >
                <Keyboard size={14} style={{ marginRight: 6 }} /> Enter details
              </button>
              <input
                ref={fileRef}
                type="file"
                accept=".csv,.xlsx,.xls"
                style={{ display: 'none' }}
                onChange={handleFilePick}
              />
            </div>
          )}

          {parseError && (
            <p style={{ color: 'var(--danger, #c44)', fontSize: '0.85rem', marginBottom: '0.75rem' }}>{parseError}</p>
          )}

          {entryMode === 'choose' && leads.length === 0 && (
            <p style={{ fontSize: '0.9rem', color: 'var(--text-muted)' }}>
              Choose how to add contacts: upload a CSV/Excel file, or enter details manually.
            </p>
          )}

          {entryMode === 'review' && (
            <>
              <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '1rem' }}>
                Review contacts below. Save to store them without launching, or start the sequence when ready.
              </p>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
                <span className="badge badge-idle">{validCount} valid · {leads.length} rows</span>
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
                          No contacts yet. Click &quot;Add Row&quot; or upload a file.
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
            </>
          )}
        </div>
        <div className="modal-footer" style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <button type="button" className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          {onSaveContacts && (
            <button
              type="button"
              className="btn-secondary"
              disabled={saving || starting || validCount === 0}
              onClick={onSaveContacts}
            >
              <Save size={16} style={{ marginRight: 6 }} />
              {saving ? 'Saving…' : 'Save contacts'}
            </button>
          )}
          <button
            type="button"
            className="btn-primary"
            disabled={starting || saving || validCount === 0}
            onClick={onConfirmStart}
          >
            {primaryIcon}
            {starting ? 'Working…' : primaryLabel}
          </button>
        </div>
      </div>
    </div>
  );
};
