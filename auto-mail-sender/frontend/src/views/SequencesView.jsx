import React, { useState, useEffect } from 'react';
import {
  Plus,
  ArrowLeft,
  LayoutTemplate,
  Save,
  RotateCcw,
  Mail,
  GripVertical,
  Trash2,
  X,
  Sliders,
  Users,
  Eye,
  Rocket,
  Pause,
  Play,
  UserPlus,
} from 'lucide-react';
import { api } from '../api';

const DEFAULT_STEPS = [
  {
    subject: '{Hello|Hi|Hey} {first_name} - quick question',
    subject_variants: [
      '{Hello|Hi|Hey} {first_name} - quick question',
      'Quick note for {first_name} at {company}',
    ],
    body: '<p>{Hi|Hello} {first_name},</p><p>Would love to connect briefly regarding {company}.</p><p>Best,<br>{sender_name}</p>',
    delay_days: 0,
    delay_hours: 0,
  },
  {
    subject: 'Re: {company} - following up',
    subject_variants: [
      'Re: {company} - following up',
      'Bumping this, {first_name}',
    ],
    body: '<p>Hi {first_name},</p><p>Just bumping this in case it got buried. Happy to share more details.</p><p>Thanks,<br>{sender_name}</p>',
    delay_days: 3,
    delay_hours: 0,
  },
  {
    subject: 'Last try - {first_name}',
    subject_variants: ['Last try - {first_name}'],
    body: "<p>Hi {first_name},</p><p>I'll keep this short — should I close the loop on this?</p><p>{sender_name}</p>",
    delay_days: 5,
    delay_hours: 0,
  },
];

export const SequencesView = ({
  showToast,
  onOpenLeadModal,
  onOpenCredentialsModal,
  onNavigateDashboard,
  setDashboardSequenceId,
  createTriggered = false,
}) => {
  const [sequences, setSequences] = useState([]);
  const [viewMode, setViewMode] = useState('list'); // 'list' | 'builder'
  const [currentSequenceId, setCurrentSequenceId] = useState(null);
  const [seqName, setSeqName] = useState('New Sequence');
  const [seqStatus, setSeqStatus] = useState('draft');

  // Templates
  const [templates, setTemplates] = useState([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState('');
  const [templateHint, setTemplateHint] = useState('');

  // Steps
  const [steps, setSteps] = useState(DEFAULT_STEPS);

  // Settings
  const [dailyLimit, setDailyLimit] = useState(100);
  const [delaySec, setDelaySec] = useState(60);
  const [windowStart, setWindowStart] = useState('09:00');
  const [windowEnd, setWindowEnd] = useState('17:00');
  const [consentRequired, setConsentRequired] = useState(true);
  const [enableReplyTracking, setEnableReplyTracking] = useState(true);

  // Preview Data
  const [previewData, setPreviewData] = useState(null);
  const [selectedFile, setSelectedFile] = useState(null);
  const [loading, setLoading] = useState(false);

  // Load Sequences & Templates
  const loadSequences = async () => {
    try {
      const res = await api.getSequences();
      if (res.success) {
        setSequences(res.sequences || []);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const loadTemplates = async () => {
    try {
      const res = await api.getTemplates();
      if (res.success) {
        setTemplates(res.templates || []);
      }
    } catch (e) {
      console.error(e);
    }
  };

  useEffect(() => {
    loadSequences();
    loadTemplates();
  }, []);

  useEffect(() => {
    if (createTriggered) {
      showCreateSequence();
    }
  }, [createTriggered]);

  const showCreateSequence = () => {
    setCurrentSequenceId(null);
    setSeqName('New Sequence');
    setSeqStatus('draft');
    setSteps(DEFAULT_STEPS);
    setDailyLimit(100);
    setDelaySec(60);
    setWindowStart('09:00');
    setWindowEnd('17:00');
    setConsentRequired(true);
    setEnableReplyTracking(true);
    setSelectedTemplateId('');
    setTemplateHint('');
    setPreviewData(null);
    setSelectedFile(null);
    setViewMode('builder');
  };

  const openSequence = async (id) => {
    try {
      const res = await api.getSequence(id);
      if (!res.success) {
        showToast(res.error, 'error');
        return;
      }
      const seq = res.sequence;
      setCurrentSequenceId(seq.id);
      setSeqName(seq.name);
      setSeqStatus(seq.status);
      setSteps(seq.steps || DEFAULT_STEPS);
      if (seq.settings) {
        setDailyLimit(seq.settings.daily_limit || 100);
        setDelaySec(seq.settings.delay_sec || 60);
        setWindowStart(seq.settings.window_start || '09:00');
        setWindowEnd(seq.settings.window_end || '17:00');
        setConsentRequired(seq.settings.consent_required !== false);
        setEnableReplyTracking(seq.settings.enable_reply_tracking !== false);
      }
      setPreviewData(null);
      setSelectedFile(null);
      setViewMode('builder');
    } catch (e) {
      showToast('Failed to open sequence', 'error');
    }
  };

  // Load template details into builder
  const handleLoadTemplate = async (templateId) => {
    setSelectedTemplateId(templateId);
    if (!templateId) {
      setTemplateHint('');
      return;
    }
    try {
      const res = await api.getTemplate(templateId);
      if (!res.success) {
        showToast(res.error, 'error');
        return;
      }
      const tpl = res.template;
      setSeqName(tpl.name);
      setDailyLimit(tpl.settings.daily_limit);
      setDelaySec(tpl.settings.delay_sec);
      setWindowStart(tpl.settings.window_start);
      setWindowEnd(tpl.settings.window_end);
      setConsentRequired(tpl.settings.consent_required);
      setEnableReplyTracking(tpl.settings.enable_reply_tracking);
      setSteps(tpl.steps);
      const src = tpl.source === 'customized' ? 'Your saved edits' : 'Default template';
      setTemplateHint(`Loaded: ${tpl.step_count} emails · ${tpl.delay_hint || 'see step delays'} · ${src}`);
      showToast(`Template "${tpl.name}" loaded — edit below and save`, 'success');
    } catch (e) {
      showToast('Failed to load template', 'error');
    }
  };

  const handleSaveTemplateEdits = async () => {
    if (!selectedTemplateId) {
      showToast('Select a template first', 'error');
      return;
    }
    const payload = {
      name: seqName,
      steps,
      settings: getSettingsPayload(),
    };
    try {
      const res = await api.saveTemplate(selectedTemplateId, payload);
      if (res.success) {
        showToast(res.message || 'Template saved', 'success');
        loadTemplates();
      } else showToast(res.error, 'error');
    } catch (e) {
      showToast('Save template failed', 'error');
    }
  };

  const handleResetTemplate = async () => {
    if (!selectedTemplateId) return;
    if (!window.confirm('Reset this template to the original default? Your edits will be lost.')) return;
    try {
      const res = await api.resetTemplate(selectedTemplateId);
      if (res.success) {
        showToast(res.message, 'success');
        handleLoadTemplate(selectedTemplateId);
        loadTemplates();
      } else showToast(res.error, 'error');
    } catch (e) {
      showToast('Reset failed', 'error');
    }
  };

  const getSettingsPayload = () => ({
    daily_limit: parseInt(dailyLimit, 10),
    delay_sec: parseInt(delaySec, 10),
    window_start: windowStart,
    window_end: windowEnd,
    consent_required: consentRequired,
    enable_reply_tracking: enableReplyTracking,
  });

  const saveSequence = async (showMsg = true) => {
    const payload = {
      name: seqName,
      steps,
      settings: getSettingsPayload(),
    };
    try {
      let res;
      if (currentSequenceId) {
        res = await api.updateSequence(currentSequenceId, payload);
      } else {
        res = await api.createSequence(payload);
      }
      if (res.success) {
        setCurrentSequenceId(res.sequence.id);
        if (showMsg) showToast('Sequence saved', 'success');
        return res.sequence.id;
      }
      showToast(res.error, 'error');
      return false;
    } catch (e) {
      showToast('Save failed: ' + e.message, 'error');
      return false;
    }
  };

  // Step reordering & editing
  const handleStepChange = (index, field, value) => {
    const updated = [...steps];
    updated[index] = { ...updated[index], [field]: value };
    setSteps(updated);
  };

  const handleAddVariant = (stepIndex) => {
    const updated = [...steps];
    const step = updated[stepIndex];
    const variants = step.subject_variants ? [...step.subject_variants] : [step.subject || ''];
    variants.push('');
    updated[stepIndex] = { ...step, subject_variants: variants, subject: variants[0] };
    setSteps(updated);
  };

  const handleRemoveVariant = (stepIndex, variantIndex) => {
    const updated = [...steps];
    const step = updated[stepIndex];
    const variants = step.subject_variants.filter((_, i) => i !== variantIndex);
    updated[stepIndex] = { ...step, subject_variants: variants, subject: variants[0] || '' };
    setSteps(updated);
  };

  const handleVariantChange = (stepIndex, variantIndex, value) => {
    const updated = [...steps];
    const step = updated[stepIndex];
    const variants = [...(step.subject_variants || [step.subject || ''])];
    variants[variantIndex] = value;
    updated[stepIndex] = { ...step, subject_variants: variants, subject: variants[0] || '' };
    setSteps(updated);
  };

  const addStep = () => {
    setSteps([
      ...steps,
      {
        subject: 'Following up - {first_name}',
        subject_variants: ['Following up - {first_name}', 'Quick bump for {first_name}'],
        body: '<p>Hi {first_name},</p><p>Just checking in.</p>',
        delay_days: 3,
        delay_hours: 0,
      },
    ]);
  };

  const removeStep = (index) => {
    setSteps(steps.filter((_, i) => i !== index));
  };

  // File Upload & Parsing
  const handleFileChange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    setSelectedFile(file);

    const fd = new FormData();
    fd.append('recipients_file', file);
    showToast('Reading Excel/CSV contacts...', 'info');

    try {
      const res = await api.parseLeads(fd);
      if (res.success && res.rows) {
        onOpenLeadModal(res.rows, async (validLeads) => {
          const seqId = await saveSequence(false);
          if (!seqId) return;
          try {
            const actRes = await api.activateSequence(seqId, { contacts: validLeads });
            if (actRes.success) {
              showToast(`Launched! ${actRes.enrolled} contact(s) enrolled. Backend sending now.`, 'success');
              setDashboardSequenceId(seqId);
              onNavigateDashboard();
            } else if (actRes.code === 'credentials_missing') {
              onOpenCredentialsModal();
            } else {
              showToast(actRes.error, 'error');
            }
          } catch (err) {
            showToast('Launch failed: ' + err.message, 'error');
          }
        });
      } else {
        showToast(res.error || 'Failed to parse file', 'error');
      }
    } catch (err) {
      showToast('File parsing failed: ' + err.message, 'error');
    }
  };

  const handleLaunchSequence = async () => {
    const seqId = await saveSequence(false);
    if (!seqId) return;

    if (!selectedFile) {
      showToast('Upload an Excel (.xls/.xlsx) or CSV file with contacts first', 'error');
      return;
    }

    const fd = new FormData();
    fd.append('recipients_csv', selectedFile);

    try {
      const res = await api.activateSequence(seqId, fd);
      if (res.success) {
        showToast(`Launched! ${res.enrolled} contacts enrolled. Backend will send automatically.`, 'success');
        setDashboardSequenceId(seqId);
        onNavigateDashboard();
      } else if (res.code === 'credentials_missing') {
        onOpenCredentialsModal();
      } else {
        showToast(res.error, 'error');
      }
    } catch (err) {
      showToast('Launch failed: ' + err.message, 'error');
    }
  };

  const handlePause = async () => {
    if (!currentSequenceId) return;
    try {
      const res = await api.pauseSequence(currentSequenceId);
      if (res.success) {
        showToast('Sequence paused', 'warning');
        setSeqStatus('paused');
      } else showToast(res.error, 'error');
    } catch (e) {
      showToast('Pause failed', 'error');
    }
  };

  const handleResume = async () => {
    if (!currentSequenceId) return;
    try {
      const res = await api.resumeSequence(currentSequenceId);
      if (res.success) {
        showToast('Sequence resumed — backend worker will continue sending', 'success');
        setSeqStatus('active');
      } else showToast(res.error, 'error');
    } catch (e) {
      showToast('Resume failed', 'error');
    }
  };

  const handlePreview = async () => {
    const seqId = await saveSequence(false);
    if (!seqId) return;

    if (!selectedFile) {
      showToast('Upload CSV to preview', 'error');
      return;
    }

    const fd = new FormData();
    fd.append('recipients_csv', selectedFile);

    try {
      const res = await api.previewSequence(seqId, fd);
      if (res.success) {
        setPreviewData(res.preview);
      } else {
        showToast(res.error, 'error');
      }
    } catch (e) {
      showToast('Preview failed', 'error');
    }
  };

  return (
    <section id="view-sequences" className="view-pane">
      {viewMode === 'list' && (
        <div id="sequences-list-view">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
            <p style={{ color: 'var(--text-muted)' }}>
              Build multi-step email sequences. Upload CSV contacts and the backend handles sending.
            </p>
            <button className="btn-primary" style={{ width: 'auto' }} onClick={showCreateSequence}>
              <Plus size={16} style={{ marginRight: 6 }} /> New Sequence
            </button>
          </div>
          <div className="sequences-grid">
            {sequences.length === 0 ? (
              <div className="card" style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)' }}>
                No sequences yet. Click "New Sequence" to start.
              </div>
            ) : (
              sequences.map((s) => (
                <div key={s.id} className="card sequence-card" onClick={() => openSequence(s.id)}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                    <h3 style={{ fontSize: '1rem' }}>{s.name}</h3>
                    <span className={`badge ${s.status === 'active' ? 'badge-running' : 'badge-idle'}`}>
                      {s.status}
                    </span>
                  </div>
                  <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', margin: '0.5rem 0' }}>
                    {s.steps?.length || 0} step(s) · {s.stats?.enrolled || 0} enrolled
                  </p>
                  <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                    Created {s.created_at ? new Date(s.created_at).toLocaleDateString() : ''}
                  </p>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {viewMode === 'builder' && (
        <div id="sequence-builder-view">
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1.5rem' }}>
            <button
              className="btn-secondary"
              style={{ width: 'auto' }}
              onClick={() => {
                setViewMode('list');
                loadSequences();
              }}
            >
              <ArrowLeft size={16} style={{ marginRight: 6 }} /> Back
            </button>
            <input
              id="seq-name-input"
              type="text"
              placeholder="Sequence name"
              value={seqName}
              onChange={(e) => setSeqName(e.target.value)}
              style={{
                flex: 1,
                fontSize: '1.1rem',
                fontWeight: 600,
                border: 'none',
                background: 'transparent',
                color: 'var(--text)',
                borderBottom: '2px solid var(--border)',
                padding: '0.25rem 0',
              }}
            />
            <span className={`badge ${seqStatus === 'active' ? 'badge-running' : 'badge-idle'}`}>
              {seqStatus}
            </span>
          </div>

          <div className="setup-container" style={{ maxWidth: '900px' }}>
            {/* APOLLO TEMPLATES PICKER */}
            <div className="card">
              <h2>
                <LayoutTemplate size={20} style={{ display: 'inline', marginRight: 8 }} /> Sequence Templates
              </h2>
              <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', margin: '0.5rem 0 1rem' }}>
                Pick a bundled template (5 emails: Day 0 → 7 → 14 → 21 → 28). Edit subjects, bodies, and delays below, then <strong>Save Template</strong> to keep your changes.
              </p>
              <div className="form-group">
                <label>Choose template</label>
                <select value={selectedTemplateId} onChange={(e) => handleLoadTemplate(e.target.value)}>
                  <option value="">— Custom sequence (build your own) —</option>
                  {templates.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name} {t.customized ? '(edited)' : ''} ({t.step_count} emails · {t.delay_hint || 'scheduled'})
                    </option>
                  ))}
                </select>
              </div>
              {selectedTemplateId && (
                <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.75rem', flexWrap: 'wrap' }}>
                  <button type="button" className="btn-secondary" style={{ width: 'auto' }} onClick={handleSaveTemplateEdits}>
                    <Save size={16} style={{ marginRight: 6 }} /> Save Template
                  </button>
                  <button type="button" className="btn-secondary" style={{ width: 'auto' }} onClick={handleResetTemplate}>
                    <RotateCcw size={16} style={{ marginRight: 6 }} /> Reset to Default
                  </button>
                </div>
              )}
              {templateHint && (
                <p style={{ fontSize: '0.8rem', color: 'var(--primary)', marginTop: '0.5rem' }}>{templateHint}</p>
              )}
            </div>

            {/* EMAIL STEPS */}
            <div className="card">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                <h2>
                  <Mail size={20} style={{ display: 'inline', marginRight: 8 }} /> Email Steps
                </h2>
                <button type="button" className="btn-secondary" style={{ width: 'auto' }} onClick={addStep}>
                  <Plus size={16} style={{ marginRight: 6 }} /> Add Step
                </button>
              </div>

              <div id="steps-container">
                {steps.map((step, index) => {
                  const variants = step.subject_variants && step.subject_variants.length ? step.subject_variants : [step.subject || ''];
                  return (
                    <div key={index} className="sequence-step-card">
                      <div className="step-header">
                        <button type="button" className="drag-handle" title="Drag to reorder">
                          <GripVertical size={16} />
                        </button>
                        <span className="step-number">Step {index + 1}</span>
                        {index > 0 ? (
                          <div className="step-delay-group">
                            <label>Wait after previous step</label>
                            <div className="step-delay-inputs">
                              <input
                                type="number"
                                className="step-delay-days"
                                min="0"
                                value={step.delay_days ?? 7}
                                onChange={(e) => handleStepChange(index, 'delay_days', parseInt(e.target.value, 10) || 0)}
                              />{' '}
                              <span>days</span>
                              <input
                                type="number"
                                className="step-delay-hours"
                                min="0"
                                max="23"
                                value={step.delay_hours ?? 0}
                                onChange={(e) => handleStepChange(index, 'delay_hours', parseInt(e.target.value, 10) || 0)}
                              />{' '}
                              <span>hours</span>
                            </div>
                          </div>
                        ) : (
                          <span className="step-delay-label">Sends immediately on launch</span>
                        )}
                        {index > 0 && (
                          <button
                            type="button"
                            className="btn-icon"
                            onClick={() => removeStep(index)}
                            title="Remove step"
                          >
                            <Trash2 size={16} />
                          </button>
                        )}
                      </div>

                      {/* Subject Variants */}
                      <div className="form-group">
                        <label>Subject (A/B variants — one picked per contact)</label>
                        <div className="subject-variants">
                          {variants.map((v, vi) => (
                            <div key={vi} className="variant-row">
                              <span className="variant-label">{String.fromCharCode(65 + vi)}</span>
                              <input
                                className="subject-variant"
                                value={v}
                                placeholder={`Subject variant ${String.fromCharCode(65 + vi)}`}
                                onChange={(e) => handleVariantChange(index, vi, e.target.value)}
                              />
                              {vi > 0 && (
                                <button
                                  type="button"
                                  className="btn-icon btn-icon-sm"
                                  onClick={() => handleRemoveVariant(index, vi)}
                                  title="Remove variant"
                                >
                                  <X size={14} />
                                </button>
                              )}
                            </div>
                          ))}
                          <button
                            type="button"
                            className="btn-link add-variant-btn"
                            onClick={() => handleAddVariant(index)}
                          >
                            + Add A/B variant
                          </button>
                        </div>
                      </div>

                      {/* Body editor */}
                      <div className="form-group">
                        <label>Body HTML/Rich Text</label>
                        <textarea
                          rows={4}
                          value={step.body || ''}
                          onChange={(e) => handleStepChange(index, 'body', e.target.value)}
                          style={{
                            width: '100%',
                            padding: '0.75rem',
                            background: 'var(--input-bg)',
                            border: '1px solid var(--input-border)',
                            borderRadius: '0.5rem',
                            color: 'var(--text)',
                            fontFamily: 'monospace',
                            fontSize: '0.85rem',
                          }}
                        />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* SENDING SETTINGS */}
            <div className="card">
              <h2>
                <Sliders size={20} style={{ display: 'inline', marginRight: 8 }} /> Sending Settings
              </h2>
              <div className="grid-2" style={{ marginTop: '1rem' }}>
                <div className="form-group">
                  <label>Delay Between Emails (sec)</label>
                  <input type="number" min="1" value={delaySec} onChange={(e) => setDelaySec(e.target.value)} />
                </div>
                <div className="form-group">
                  <label>Daily Limit</label>
                  <input type="number" min="1" value={dailyLimit} onChange={(e) => setDailyLimit(e.target.value)} />
                </div>
              </div>
              <div className="grid-2">
                <div className="form-group">
                  <label>Working Hours Start (HH:MM)</label>
                  <input value={windowStart} onChange={(e) => setWindowStart(e.target.value)} />
                </div>
                <div className="form-group">
                  <label>Working Hours End (HH:MM)</label>
                  <input value={windowEnd} onChange={(e) => setWindowEnd(e.target.value)} />
                </div>
              </div>
              <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)', marginTop: '-0.5rem' }}>
                Emails only send inside this window each day.
              </p>
              <div className="form-group" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <input
                  type="checkbox"
                  style={{ width: 'auto' }}
                  checked={consentRequired}
                  onChange={(e) => setConsentRequired(e.target.checked)}
                />
                <label style={{ margin: 0 }}>Require <code>consent=true</code> in CSV</label>
              </div>
              <div className="form-group" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <input
                  type="checkbox"
                  style={{ width: 'auto' }}
                  checked={enableReplyTracking}
                  onChange={(e) => setEnableReplyTracking(e.target.checked)}
                />
                <label style={{ margin: 0 }}>Auto-stop on reply</label>
              </div>
            </div>

            {/* CONTACTS FILE DROPZONE */}
            <div className="card">
              <h2>
                <Users size={20} style={{ display: 'inline', marginRight: 8 }} /> Contacts (CSV / Excel)
              </h2>
              <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', margin: '0.5rem 0 1rem' }}>
                Required columns: <code>email</code>. Optional: <code>first_name</code>, <code>company</code>, <code>consent</code>, etc.
              </p>
              <input type="file" accept=".csv,.xlsx,.xls" onChange={handleFileChange} />
            </div>

            <div className="grid-2">
              <button type="button" className="btn-secondary" onClick={() => saveSequence(true)}>
                <Save size={16} style={{ marginRight: 6 }} /> Save Draft
              </button>
              <button type="button" className="btn-secondary" onClick={handlePreview}>
                <Eye size={16} style={{ marginRight: 6 }} /> Preview
              </button>
            </div>

            {seqStatus === 'draft' ? (
              <button
                type="button"
                className="btn-primary"
                style={{ marginTop: '0.75rem' }}
                onClick={handleLaunchSequence}
              >
                <Rocket size={16} style={{ marginRight: 6 }} /> Launch Sequence
              </button>
            ) : (
              <div style={{ display: 'flex', gap: '0.75rem', marginTop: '0.75rem' }}>
                {seqStatus === 'active' && (
                  <button type="button" className="btn-danger" style={{ flex: 1 }} onClick={handlePause}>
                    <Pause size={16} style={{ marginRight: 6 }} /> Pause
                  </button>
                )}
                {seqStatus === 'paused' && (
                  <button type="button" className="btn-primary" style={{ flex: 1 }} onClick={handleResume}>
                    <Play size={16} style={{ marginRight: 6 }} /> Resume
                  </button>
                )}
              </div>
            )}

            {/* PREVIEW RESULTS */}
            {previewData && (
              <div className="card" style={{ marginTop: '1rem' }}>
                <h2>Preview</h2>
                {previewData.map((p, idx) => (
                  <div key={idx} className="recipient-item" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: '0.5rem' }}>
                    <div>
                      <strong>Step {p.step} → {p.to}</strong>
                    </div>
                    <div>
                      <strong>Subject:</strong> {p.subject}{' '}
                      <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                        (variant: {p.subject_variant || ''})
                      </span>
                    </div>
                    <div
                      style={{
                        width: '100%',
                        padding: '0.75rem',
                        background: 'var(--bg)',
                        border: '1px solid var(--border)',
                        borderRadius: '6px',
                        fontSize: '0.8rem',
                      }}
                      dangerouslySetInnerHTML={{ __html: p.body }}
                    />
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
};
