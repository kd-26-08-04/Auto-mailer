import React, { useState, useEffect } from 'react';
import { useAuth } from './context/AuthContext';
import { Sidebar } from './components/Sidebar';
import { Header } from './components/Header';
import { ToastContainer } from './components/ToastContainer';
import { LeadReviewModal } from './components/LeadReviewModal';
import { CredentialsModal } from './components/CredentialsModal';
import { LoginView } from './views/LoginView';
import { RegisterView } from './views/RegisterView';
import { DashboardView } from './views/DashboardView';
import { SequencesView } from './views/SequencesView';
import { InboxView } from './views/InboxView';
import { AnalyticsView } from './views/AnalyticsView';
import { SettingsView } from './views/SettingsView';
import { api } from './api';

export function App() {
  const { user, loading } = useAuth();
  const [authView, setAuthView] = useState('login');
  const [currentView, setCurrentView] = useState('dashboard');
  const [dashboardSeqId, setDashboardSeqId] = useState(null);

  const [isDarkMode, setIsDarkMode] = useState(() => localStorage.getItem('theme') !== 'light');
  const [toasts, setToasts] = useState([]);

  const [leadModalOpen, setLeadModalOpen] = useState(false);
  const [parsedLeads, setParsedLeads] = useState([]);
  const [onConfirmLeadCallback, setOnConfirmLeadCallback] = useState(null);
  const [onSaveContactsCallback, setOnSaveContactsCallback] = useState(null);
  const [leadModalMode, setLeadModalMode] = useState('launch');
  const [leadLaunching, setLeadLaunching] = useState(false);
  const [leadSaving, setLeadSaving] = useState(false);

  const [credentialsModalOpen, setCredentialsModalOpen] = useState(false);
  const [scanningReplies, setScanningReplies] = useState(false);
  const [createSeqTrigger, setCreateSeqTrigger] = useState(false);

  useEffect(() => {
    if (isDarkMode) {
      document.body.classList.add('dark-mode');
    } else {
      document.body.classList.remove('dark-mode');
    }
  }, [isDarkMode]);

  const toggleTheme = () => {
    setIsDarkMode((prev) => {
      const next = !prev;
      localStorage.setItem('theme', next ? 'dark' : 'light');
      return next;
    });
  };

  const showToast = (msg, type = 'info') => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, msg, type }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4000);
  };

  const handleScanReplies = async () => {
    setScanningReplies(true);
    showToast('Scanning Gmail inbox for replies...', 'info');
    try {
      const res = await api.checkReplies(new FormData());
      if (res.status === 'error') {
        showToast(res.message || 'Scan failed', 'error');
      } else {
        showToast(res.message || 'Scan completed successfully!', 'success');
      }
    } catch (e) {
      showToast('Scan failed: ' + e.message, 'error');
    } finally {
      setScanningReplies(false);
    }
  };

  const handleOpenLeadModal = (leads, onConfirm, options = {}) => {
    setParsedLeads(leads || []);
    setOnConfirmLeadCallback(() => onConfirm);
    setOnSaveContactsCallback(options.onSave ? () => options.onSave : null);
    setLeadModalMode(options.mode || 'launch');
    setLeadModalOpen(true);
  };

  const handleConfirmLeadStart = async () => {
    if (!onConfirmLeadCallback) return;
    setLeadLaunching(true);
    try {
      await onConfirmLeadCallback(parsedLeads);
      setLeadModalOpen(false);
    } finally {
      setLeadLaunching(false);
    }
  };

  const handleSaveContacts = async () => {
    if (!onSaveContactsCallback) return;
    setLeadSaving(true);
    try {
      await onSaveContactsCallback(parsedLeads);
      // Do NOT auto-close — LeadReviewModal will show success banner; user closes manually
    } finally {
      setLeadSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="auth-body" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>
        <div className="card" style={{ padding: '2rem', textAlign: 'center' }}>
          <h2>Loading...</h2>
        </div>
      </div>
    );
  }

  if (!user) {
    return (
      <>
        <ToastContainer toasts={toasts} />
        {authView === 'login' ? (
          <LoginView
            onNavigateRegister={() => setAuthView('register')}
            showToast={showToast}
            isDarkMode={isDarkMode}
            toggleTheme={toggleTheme}
          />
        ) : (
          <RegisterView
            onNavigateLogin={() => setAuthView('login')}
            showToast={showToast}
            isDarkMode={isDarkMode}
            toggleTheme={toggleTheme}
          />
        )}
      </>
    );
  }

  return (
    <div className="app-layout">
      <ToastContainer toasts={toasts} />
      <Sidebar currentView={currentView} switchView={setCurrentView} />

      <main className="main-content">
        <Header
          currentView={currentView}
          isDarkMode={isDarkMode}
          toggleTheme={toggleTheme}
          onScanReplies={handleScanReplies}
          scanning={scanningReplies}
        />

        {currentView === 'dashboard' && (
          <DashboardView
            sequenceId={dashboardSeqId}
            onNavigateSequences={() => setCurrentView('sequences')}
            onShowCreateSequence={() => {
              setCreateSeqTrigger(true);
              setCurrentView('sequences');
            }}
          />
        )}

        {currentView === 'sequences' && (
          <SequencesView
            showToast={showToast}
            onOpenLeadModal={handleOpenLeadModal}
            onOpenCredentialsModal={() => setCredentialsModalOpen(true)}
            onNavigateDashboard={() => setCurrentView('dashboard')}
            setDashboardSequenceId={setDashboardSeqId}
            createTriggered={createSeqTrigger}
            onCreateHandled={() => setCreateSeqTrigger(false)}
          />
        )}

        {currentView === 'inbox' && <InboxView sequenceId={dashboardSeqId} />}

        {currentView === 'analytics' && <AnalyticsView sequenceId={dashboardSeqId} />}

        {currentView === 'settings' && <SettingsView showToast={showToast} />}

        <LeadReviewModal
          isOpen={leadModalOpen}
          onClose={() => setLeadModalOpen(false)}
          leads={parsedLeads}
          setLeads={setParsedLeads}
          onConfirmStart={handleConfirmLeadStart}
          onSaveContacts={onSaveContactsCallback ? handleSaveContacts : null}
          starting={leadLaunching}
          saving={leadSaving}
          mode={leadModalMode}
        />

        <CredentialsModal
          isOpen={credentialsModalOpen}
          onClose={() => setCredentialsModalOpen(false)}
          onSuccess={() => {
            showToast('Credentials updated successfully', 'success');
          }}
          showToast={showToast}
        />
      </main>
    </div>
  );
}
export default App;
