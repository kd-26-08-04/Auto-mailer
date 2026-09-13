import React, { useState } from 'react';
import { User, Lock, Eye, EyeOff, ArrowRight, AlertCircle, Sun, Moon } from 'lucide-react';
import { useAuth } from '../context/AuthContext';

export const LoginView = ({ onNavigateRegister, showToast, isDarkMode, toggleTheme }) => {
  const { login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setErrorMsg('');
    setSubmitting(true);
    try {
      await login(username, password);
      showToast('Logged in successfully', 'success');
    } catch (err) {
      setErrorMsg(err.message || 'Invalid credentials.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="auth-body">
      {/* Glowing Animated Background Blobs */}
      <div className="bg-blobs">
        <div className="blob blob-1"></div>
        <div className="blob blob-2"></div>
        <div className="blob blob-3"></div>
      </div>

      <div className="auth-container">
        <div style={{ position: 'absolute', top: 24, right: 24, zIndex: 100 }}>
          <button
            type="button"
            className="btn-secondary"
            onClick={toggleTheme}
            style={{ width: '38px', height: '38px', padding: 0, borderRadius: '50%', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}
            title="Toggle theme"
          >
            {isDarkMode ? <Sun size={18} /> : <Moon size={18} />}
          </button>
        </div>

        <div className="auth-card">

          <div className="auth-header">
            <h1>
              <img src="/favicon.jpeg" alt="EISTATECH Logo" /> EISTATECH
            </h1>
            <p>Welcome back! Please login to your account.</p>
          </div>

          {errorMsg && (
            <div className="auth-flash auth-flash-error">
              <AlertCircle size={18} />
              <span>{errorMsg}</span>
            </div>
          )}

          <form onSubmit={handleSubmit}>
            <div className="form-group">
              <label htmlFor="username">Username</label>
              <div className="input-icon-wrapper">
                <User size={18} />
                <input
                  type="text"
                  id="username"
                  name="username"
                  placeholder="Enter your username"
                  required
                  autoFocus
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </div>
            </div>

            <div className="form-group">
              <label htmlFor="password">Password</label>
              <div className="input-icon-wrapper">
                <Lock size={18} />
                <input
                  type={showPassword ? 'text' : 'password'}
                  id="password"
                  name="password"
                  placeholder="Enter your password"
                  className="input-with-toggle"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <button
                  type="button"
                  id="toggle-password"
                  className="toggle-password-btn"
                  title="Toggle password visibility"
                  onClick={() => setShowPassword(!showPassword)}
                >
                  {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </div>

            <button type="submit" className="auth-btn" disabled={submitting}>
              <span>{submitting ? 'Logging in...' : 'Login'}</span>
              <ArrowRight size={18} />
            </button>
          </form>

          <div className="auth-footer">
            Don't have an account?{' '}
            <a
              href="#register"
              onClick={(e) => {
                e.preventDefault();
                onNavigateRegister();
              }}
            >
              Register here
            </a>
          </div>
        </div>
      </div>
    </div>
  );
};
