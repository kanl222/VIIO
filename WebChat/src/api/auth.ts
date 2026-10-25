import { apiFetch } from './client';
import type { User } from '../types';

export async function login(username: string, password: string): Promise<User> {
  const data = await apiFetch<{ user: User }>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
  return data.user;
}

export async function register(username: string, password: string): Promise<User> {
  const data = await apiFetch<{ user: User }>('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
  return data.user;
}

export async function logout(): Promise<void> {
  await apiFetch<{ ok: boolean }>('/api/auth/logout', {
    method: 'POST',
  });
}

export async function getMe(): Promise<User> {
  const data = await apiFetch<{ user: User }>('/api/auth/me', {
    method: 'GET',
  });
  return data.user;
}
