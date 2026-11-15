import type pg from 'pg';
import { hashPassword } from './auth.js';

export interface BootstrapAdminConfig {
  username: string;
  password: string;
}

export function readBootstrapAdminConfig(env: NodeJS.ProcessEnv = process.env): BootstrapAdminConfig | null {
  const username = (env.BOOTSTRAP_ADMIN_USERNAME || '').trim();
  const password = env.BOOTSTRAP_ADMIN_PASSWORD || '';
  if (!username && !password) return null;
  if (username.length < 3 || username.length > 64 || password.length < 12 || Buffer.byteLength(password, 'utf8') > 72) {
    throw new Error('Первый администратор: задайте BOOTSTRAP_ADMIN_USERNAME (3–64 символа) и BOOTSTRAP_ADMIN_PASSWORD (от 12 символов, до 72 байт UTF-8).');
  }
  return { username, password };
}

/** Create only the first admin. Never promote an existing account or reset its password. */
export async function bootstrapAdmin(pool: pg.Pool, config: BootstrapAdminConfig | null): Promise<'disabled' | 'existing' | 'created'> {
  if (!config) return 'disabled';
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    await client.query("SET LOCAL statement_timeout = '10s'");
    // Та же блокировка транзакции, что и в редакторе административных ролей.
    await client.query('SELECT pg_advisory_xact_lock(864210)');
    const admins = await client.query("SELECT id FROM users WHERE role = 'admin' LIMIT 1");
    if (admins.rows.length) {
      await client.query('COMMIT');
      return 'existing';
    }
    const collision = await client.query('SELECT id FROM users WHERE username = $1', [config.username]);
    if (collision.rows.length) {
      throw new Error('Не удалось создать первого администратора: логин уже занят. Выберите другой BOOTSTRAP_ADMIN_USERNAME или назначьте права вручную.');
    }
    const passwordHash = await hashPassword(config.password);
    await client.query(
      `INSERT INTO users (username, password_hash, role, departments)
       VALUES ($1, $2, 'admin', $3)`,
      [config.username, passwordHash, ['general']],
    );
    await client.query('COMMIT');
    return 'created';
  } catch (error) {
    await client.query('ROLLBACK').catch(() => {});
    throw error;
  } finally {
    client.release();
  }
}
