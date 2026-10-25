import { useState, useEffect, useCallback } from 'react';
import type { User } from '../types';
import * as authApi from '../api/auth';

export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const checkAuth = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const currentUser = await authApi.getMe();
      setUser(currentUser);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  const login = async (username: string, password: string) => {
    setError(null);
    try {
      const loggedIn = await authApi.login(username, password);
      setUser(loggedIn);
      return loggedIn;
    } catch (err: any) {
      setError(err.message || 'Ошибка входа');
      throw err;
    }
  };

  const register = async (username: string, password: string) => {
    setError(null);
    try {
      const registered = await authApi.register(username, password);
      setUser(registered);
      return registered;
    } catch (err: any) {
      setError(err.message || 'Ошибка регистрации');
      throw err;
    }
  };

  const logout = async () => {
    try {
      await authApi.logout();
    } finally {
      setUser(null);
    }
  };

  return {
    user,
    loading,
    error,
    login,
    register,
    logout,
    checkAuth,
  };
}
