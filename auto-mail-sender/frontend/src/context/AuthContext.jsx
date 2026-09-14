import React, { createContext, useContext, useState, useEffect, useRef } from 'react';
import { api } from '../api';

const AuthContext = createContext(null);

const KEEP_ALIVE_INTERVAL_MS = 5 * 60 * 1000; // Ping every 5 minutes to keep Render alive
const API_BASE = import.meta.env.VITE_API_BASE_URL || '';

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  // If user was previously logged in, show loader while validating session.
  // If user is not logged in (e.g. first visit), show Login page INSTANTLY (0ms delay)!
  const [loading, setLoading] = useState(() => {
    return localStorage.getItem('auth_active') === 'true';
  });
  const keepAliveRef = useRef(null);

  const pingServer = async () => {
    try {
      await fetch(`${API_BASE}/api/health`, { method: 'GET', cache: 'no-store' });
    } catch (_) {
      // silent
    }
  };

  const checkAuth = async () => {
    try {
      const data = await api.checkAuth();
      if (data && data.authenticated) {
        setUser({
          user_id: data.user_id,
          username: data.username,
          full_name: data.full_name,
        });
        localStorage.setItem('auth_active', 'true');
      } else {
        setUser(null);
        localStorage.removeItem('auth_active');
      }
    } catch (err) {
      setUser(null);
      localStorage.removeItem('auth_active');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // 1. Initial pre-warm ping immediately when page loads
    pingServer();

    // 2. Continuous keep-alive interval every 5 minutes while browser tab is open
    keepAliveRef.current = setInterval(pingServer, KEEP_ALIVE_INTERVAL_MS);

    // 3. Tab visibility change ping — when user refocuses the tab, ping immediately
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        pingServer();
      }
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);

    // 4. Validate auth session
    checkAuth();

    return () => {
      if (keepAliveRef.current) clearInterval(keepAliveRef.current);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, []);

  const login = async (username, password) => {
    const res = await api.login({ username, password });
    if (res.success && res.user) {
      setUser(res.user);
      localStorage.setItem('auth_active', 'true');
      return res;
    }
    throw new Error(res.error || 'Login failed');
  };

  const register = async (userData) => {
    const res = await api.register(userData);
    return res;
  };

  const logout = async () => {
    try {
      await api.logout();
    } catch (err) {
      console.error('Logout error:', err);
    } finally {
      setUser(null);
      localStorage.removeItem('auth_active');
    }
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout, checkAuth }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => useContext(AuthContext);
