import { test } from 'node:test';
import assert from 'node:assert/strict';
import { bootstrapAdmin, readBootstrapAdminConfig } from '../dist-server/bootstrapAdmin.js';
import { verifyPassword } from '../dist-server/auth.js';

const config = { username: 'first-admin', password: 'test-only-password-42' };

function fixture(users = [], failInsert = false) {
  const calls = [];
  let released = false;
  const client = {
    async query(sql, args = []) {
      calls.push({ sql, args });
      if (sql.includes("WHERE role = 'admin'")) return { rows: users.filter(u => u.role === 'admin') };
      if (sql.includes('WHERE username = $1')) return { rows: users.filter(u => u.username === args[0]) };
      if (sql.includes('INSERT INTO users')) {
        if (failInsert) throw new Error('test insert failure');
        users.push({ username: args[0], password_hash: args[1], role: 'admin', departments: args[2] });
      }
      return { rows: [] };
    },
    release() { released = true; },
  };
  return { users, calls, get released() { return released; }, pool: { async connect() { return client; } } };
}

test('bootstrap is opt-in and validates credentials without exposing the password', () => {
  assert.equal(readBootstrapAdminConfig({}), null);
  assert.equal(readBootstrapAdminConfig({ BOOTSTRAP_ADMIN_USERNAME: '', BOOTSTRAP_ADMIN_PASSWORD: '' }), null);
  assert.deepEqual(readBootstrapAdminConfig({ BOOTSTRAP_ADMIN_USERNAME: ' first-admin ', BOOTSTRAP_ADMIN_PASSWORD: config.password }), config);
  for (const env of [
    { BOOTSTRAP_ADMIN_USERNAME: 'admin' },
    { BOOTSTRAP_ADMIN_PASSWORD: config.password },
    { BOOTSTRAP_ADMIN_USERNAME: 'ab', BOOTSTRAP_ADMIN_PASSWORD: config.password },
    { BOOTSTRAP_ADMIN_USERNAME: 'admin', BOOTSTRAP_ADMIN_PASSWORD: 'short' },
    { BOOTSTRAP_ADMIN_USERNAME: 'admin', BOOTSTRAP_ADMIN_PASSWORD: 'я'.repeat(37) },
  ]) {
    assert.throws(() => readBootstrapAdminConfig(env), error => !error.message.includes(config.password));
  }
});

test('empty configuration does not access the database', async () => {
  assert.equal(await bootstrapAdmin({ connect() { throw new Error('must not connect'); } }, null), 'disabled');
});

test('first admin gets a hashed password; repeated startup does not overwrite it', async () => {
  const f = fixture();
  assert.equal(await bootstrapAdmin(f.pool, config), 'created');
  assert.equal(f.users.length, 1);
  assert.equal(f.users[0].role, 'admin');
  assert.deepEqual(f.users[0].departments, ['general']);
  assert.notEqual(f.users[0].password_hash, config.password);
  assert.equal(await verifyPassword(config.password, f.users[0].password_hash), true);
  const hash = f.users[0].password_hash;
  assert.equal(await bootstrapAdmin(f.pool, { ...config, password: 'different-password' }), 'existing');
  assert.equal(f.users[0].password_hash, hash);
  assert.equal(f.calls.filter(c => c.sql.includes('INSERT INTO users')).length, 1);
  assert.equal(f.calls.some(c => c.sql.includes('pg_advisory_xact_lock')), true);
  assert.equal(f.calls.some(c => c.args.includes(config.password)), false);
  assert.equal(f.released, true);
});

test('any existing administrator prevents creation of another one', async () => {
  const f = fixture([{ username: 'another-admin', role: 'admin' }]);
  assert.equal(await bootstrapAdmin(f.pool, config), 'existing');
  assert.equal(f.users.length, 1);
});

test('a registered employee is never silently promoted', async () => {
  const f = fixture([{ username: config.username, role: 'employee', password_hash: 'original' }]);
  await assert.rejects(bootstrapAdmin(f.pool, config), /логин уже занят/);
  assert.equal(f.users[0].role, 'employee');
  assert.equal(f.users[0].password_hash, 'original');
  assert.equal(f.calls.at(-1).sql, 'ROLLBACK');
  assert.equal(f.released, true);
});

test('database failure rolls back and releases the connection', async () => {
  const f = fixture([], true);
  await assert.rejects(bootstrapAdmin(f.pool, config), /test insert failure/);
  assert.equal(f.calls.at(-1).sql, 'ROLLBACK');
  assert.equal(f.released, true);
});
