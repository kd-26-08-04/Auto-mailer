import React, { useState } from 'react';
import { UserCheck, Phone, User, Lock, ShieldCheck, Check, Eye, EyeOff, ArrowRight, AlertCircle, Sun, Moon } from 'lucide-react';
import { useAuth } from '../context/AuthContext';

export const RegisterView = ({ onNavigateLogin, showToast, isDarkMode, toggleTheme }) => {
  const { register } = useAuth();
  const [fullName, setFullName] = useState('');
  const [phone, setPhone] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const isPasswordMatch = password && confirmPassword && password === confirmPassword;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!isPasswordMatch) {
      setErrorMsg('Passwords do not match.');
      return;
    }
    setErrorMsg('');
    setSubmitting(true);
    try {
      const res = await register({
        full_name: fullName,
        phone,
        username,
        password,
        confirm_password: confirmPassword,
      });
      if (res.success) {
        showToast('Registration successful! Please login.', 'success');
        onNavigateLogin();
      } else {
        setErrorMsg(res.error || 'Registration failed');
      }
    } catch (err) {
      setErrorMsg(err.message || 'Registration failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="auth-body">
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

        <div className="auth-card auth-card-register">

          <div className="auth-header">
            <h1>
              <img src="/favicon.jpeg" alt="EISTATECH Logo" /> EISTATECH
            </h1>
            <p>Create your new account to get started.</p>
          </div>

          {errorMsg && (
            <div className="auth-flash auth-flash-error">
              <AlertCircle size={18} />
              <span>{errorMsg}</span>
            </div>
          )}

          <form onSubmit={handleSubmit}>
            <div className="form-group">
              <label htmlFor="full_name">Full Name</label>
              <div className="input-icon-wrapper">
                <UserCheck size={18} />
                <input
                  type="text"
                  id="full_name"
                  placeholder="Enter your full name"
                  required
                  autoFocus
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                />
              </div>
            </div>

            <div className="form-group">
              <label htmlFor="phone">Phone Number</label>
              <div className="input-icon-wrapper">
                <Phone size={18} />
                <input
                  type="tel"
                  id="phone"
                  placeholder="Enter your phone number"
                  required
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                />
              </div>
            </div>

            <div className="form-group">
              <label htmlFor="username">Username</label>
              <div className="input-icon-wrapper">
                <User size={18} />
                <input
                  type="text"
                  id="username"
                  placeholder="Choose a unique username"
                  required
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
                  placeholder="Choose a password"
                  className="input-with-toggle"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <button
                  type="button"
                  className="toggle-password-btn"
                  onClick={() => setShowPassword(!showPassword)}
                >
                  {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </div>

            <div className="form-group">
              <label htmlFor="confirm_password">Confirm Password</label>
              <div className="input-icon-wrapper">
                <ShieldCheck size={18} />
                <input
                  type={showConfirmPassword ? 'text' : 'password'}
                  id="confirm_password"
                  placeholder="Confirm your password"
                  className="input-with-toggle-match"
                  style={{ borderColor: isPasswordMatch ? 'var(--success)' : '' }}
                  required
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                />
                {isPasswordMatch && (
                  <span className="input-match-indicator" style={{ display: 'inline-flex' }} title="Passwords match">
                    <Check size={18} />
                  </span>
                )}
                <button
                  type="button"
                  className="toggle-password-btn"
                  onClick={() => setShowConfirmPassword(!showConfirmPassword)}
                >
                  {showConfirmPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </div>

            <button type="submit" className="auth-btn" disabled={!isPasswordMatch || submitting}>
              <span>{submitting ? 'Registering...' : 'Register'}</span>
              <ArrowRight size={18} />
            </button>
          </form>

          <div className="auth-footer">
            Already have an account?{' '}
            <a
              href="#login"
              onClick={(e) => {
                e.preventDefault();
                onNavigateLogin();
              }}
            >
              Login here
            </a>
          </div>
        </div>
      </div>
    </div>
  );
};
