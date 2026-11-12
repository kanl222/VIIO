import { useState, useEffect, useCallback } from 'react';

const STORAGE_KEY = 'viio-diagnostics-theme-v1';

export function useTheme() {
  const [dark, setDark] = useState<boolean>(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored === 'dark') return true;
      if (stored === 'light') return false;
      return window.matchMedia('(prefers-color-scheme: dark)').matches;
    } catch {
      return false;
    }
  });

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark);
    try {
      localStorage.setItem(STORAGE_KEY, dark ? 'dark' : 'light');
    } catch {
      // storage unavailable
    }
  }, [dark]);

  const toggleTheme = useCallback(() => {
    setDark(prev => !prev);
  }, []);

  return { dark, toggleTheme };
}
