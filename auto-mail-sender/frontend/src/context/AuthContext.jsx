import React, { createContext, useContext, useState, useEffect, useRef } from 'react';
import { api } from '../api';

const AuthContext = createContext(null);

const KEEP_ALIVE_INTERVAL_MS = 10 * 60 * 1000; // 10 minutes

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const keepAliveRef = useRef(null);

  const startKeepAlive = () => {
    // Ping /api/health every 10 min to prevent Render free tier sleep
    if (keepAliveRef.current) return; // already running
    keepAliveRef.current = setInterval(async () => {
      try {
        await fetch('/api/health');
      } catch (_) {
        // silent — server might be temporarily down
      }
    }, KEEP_ALIVE_INTERVAL_MS);
  };

  const stopKeepAlive = () => {
    if (keepAliveRef.current) {
      clearInterval(keepAliveRef.current);
      keepAliveRef.current = null;
    }
  };

  const checkAuth = async () => {
    try {
      const data = await api.checkAuth();
      if (data.authenticated) {
        setUser({
          user_id: data.user_id,
          username: data.username,
          full_name: data.full_name,
        });
        startKeepAlive();
      } else {
        setUser(null);
        stopKeepAlive();
      }
    } catch (err) {
      setUser(null);
      stopKeepAlive();
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    checkAuth();
    return () => stopKeepAlive(); // cleanup on unmount
  }, []);

  const login = async (username, password) => {
    const res = await api.login({ username, password });
    if (res.success && res.user) {
      setUser(res.user);
      startKeepAlive();
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
      stopKeepAlive();
    }
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout, checkAuth }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => useContext(AuthContext);
