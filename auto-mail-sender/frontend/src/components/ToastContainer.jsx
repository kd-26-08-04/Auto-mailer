import React from 'react';
import { CheckCircle, AlertTriangle, AlertCircle, Info } from 'lucide-react';

const icons = {
  success: CheckCircle,
  error: AlertTriangle,
  warning: AlertCircle,
  info: Info,
};

export const ToastContainer = ({ toasts = [] }) => {
  return (
    <div
      id="toast-container"
      style={{
        position: 'fixed',
        top: 20,
        right: 20,
        zIndex: 9999,
        display: 'flex',
        flexDirection: 'column',
        gap: 12,
        pointerEvents: 'none',
      }}
    >
      {toasts.map((toast) => {
        const IconComponent = icons[toast.type] || Info;
        return (
          <div
            key={toast.id}
            className={`toast toast-${toast.type}`}
            style={{ pointerEvents: 'auto' }}
          >
            <IconComponent size={18} />
            <div style={{ flex: 1, fontSize: '0.875rem', fontWeight: 600 }}>
              {toast.msg}
            </div>
          </div>
        );
      })}
    </div>
  );
};
