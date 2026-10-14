import { randomUUID } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import initSqlJs, { type Database as SqlDatabase } from 'sql.js';
import type { Account, AccountInput, AccountUpdate, AuditEvent, BrowserProfile, ProfileInput, ProfileUpdate } from '../shared/contracts';
import { accountInput, accountUpdate, id, profileInput, profileUpdate } from './validators';

const MIGRATION_1 = [
'CREATE TABLE IF NOT EXISTS browser_profiles (id TEXT PRIMARY KEY, label TEXT NOT NULL, homeUrl TEXT NOT NULL, storagePartition TEXT NOT NULL UNIQUE, createdAt TEXT NOT NULL, updatedAt TEXT NOT NULL);',
'CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, provider TEXT NOT NULL, displayName TEXT NOT NULL, parentAccountId TEXT REFERENCES accounts(id) ON DELETE SET NULL, profileId TEXT REFERENCES browser_profiles(id) ON DELETE SET NULL, status TEXT NOT NULL CHECK(status IN (\'draft\',\'ready\',\'paused\')), createdAt TEXT NOT NULL, updatedAt TEXT NOT NULL);',
'CREATE INDEX IF NOT EXISTS accounts_parent ON accounts(parentAccountId);',
'CREATE INDEX IF NOT EXISTS accounts_profile ON accounts(profileId);',
'CREATE TABLE IF NOT EXISTS audit_events (id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, action TEXT NOT NULL, entityType TEXT NOT NULL, entityId TEXT NOT NULL, result TEXT NOT NULL, timestamp TEXT NOT NULL);',
'CREATE INDEX IF NOT EXISTS audit_events_timestamp ON audit_events(timestamp DESC);',
].join('\n');

export class Repository {
  private constructor(private readonly sql: SqlDatabase, private readonly filename: string) {}

  static async open(filename: string): Promise<Repository> {
    const wasmBinary = Uint8Array.from(readFileSync(require.resolve('sql.js/dist/sql-wasm.wasm'))).buffer;
    const SQL = await initSqlJs({ wasmBinary });
    const sql = existsSync(filename)
      ? new SQL.Database(new Uint8Array(readFileSync(filename)))
      : new SQL.Database();
    const repo = new Repository(sql, filename);
    repo.sql.run('PRAGMA foreign_keys = ON');
    repo.sql.run('CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, appliedAt TEXT NOT NULL)');
    const existing = repo.rows<{ version: number }>('SELECT version FROM schema_migrations');
    if (!existing.some((r) => r.version === 1)) {
      repo.mutate(() => {
        repo.sql.exec(MIGRATION_1);
        repo.sql.run('INSERT INTO schema_migrations(version, appliedAt) VALUES (?, ?)', [1, new Date().toISOString()]);
      });
    }
    return repo;
  }

  close() { this.sql.close(); }

  private rows<T>(sql: string, params: Array<string | number | null> = []): T[] {
    const statement = this.sql.prepare(sql, params);
    try {
      const result: T[] = [];
      while (statement.step()) result.push(statement.getAsObject() as T);
      return result;
    } finally { statement.free(); }
  }

  private persist() {
    mkdirSync(dirname(this.filename), { recursive: true, mode: 0o700 });
    const temp = this.filename + '.tmp';
    writeFileSync(temp, Buffer.from(this.sql.export()), { mode: 0o600 });
    renameSync(temp, this.filename);
    this.sql.run('PRAGMA foreign_keys = ON');
  }

  private mutate<T>(fn: () => T): T {
    this.sql.run('BEGIN IMMEDIATE');
    try {
      const result = fn();
      this.sql.run('COMMIT');
      this.persist();
      return result;
    } catch (error) {
      try { this.sql.run('ROLLBACK'); } catch { /* Commit may already be complete. */ }
      throw error;
    }
  }

  private audit(action: string, entityType: string, entityId: string) {
    this.sql.run(
      'INSERT INTO audit_events (actor, action, entityType, entityId, result, timestamp) VALUES (?, ?, ?, ?, ?, ?)',
      ['local-user', action, entityType, entityId, 'ok', new Date().toISOString()],
    );
  }

  listProfiles(): BrowserProfile[] {
    return this.rows<BrowserProfile>('SELECT * FROM browser_profiles ORDER BY createdAt DESC');
  }
  profile(key: string): BrowserProfile {
    const result = this.rows<BrowserProfile>('SELECT * FROM browser_profiles WHERE id = ?', [id(key)])[0];
    if (!result) throw new Error('Perfil não encontrado.');
    return result;
  }
  createProfile(payload: unknown): BrowserProfile {
    const input = profileInput(payload), uid = randomUUID(), now = new Date().toISOString();
    const profile: BrowserProfile = {
      id: uid, label: input.label, homeUrl: input.homeUrl,
      storagePartition: 'persist:orbita-' + uid, createdAt: now, updatedAt: now,
    };
    return this.mutate(() => {
      this.sql.run(
        'INSERT INTO browser_profiles(id,label,homeUrl,storagePartition,createdAt,updatedAt) VALUES (?,?,?,?,?,?)',
        [profile.id,profile.label,profile.homeUrl,profile.storagePartition,now,now],
      );
      this.audit('profile.create', 'profile', uid);
      return profile;
    });
  }
  updateProfile(payload: unknown): BrowserProfile {
    const input = profileUpdate(payload), old = this.profile(input.id), now = new Date().toISOString();
    return this.mutate(() => {
      this.sql.run('UPDATE browser_profiles SET label=?, homeUrl=?, updatedAt=? WHERE id=?',
        [input.label ?? old.label,input.homeUrl ?? old.homeUrl,now,input.id]);
      this.audit('profile.update','profile',input.id);
      return this.profile(input.id);
    });
  }
  removeProfile(key: string): void {
    this.profile(key);
    this.mutate(() => {
      this.sql.run('DELETE FROM browser_profiles WHERE id=?', [key]);
      this.audit('profile.delete', 'profile', key);
    });
  }
  recordProfileAction(key: string, action: 'open'|'close'|'clear') {
    this.profile(key);
    this.mutate(() => this.audit('profile.' + action, 'profile', key));
  }

  listAccounts(): Account[] {
    return this.rows<Account>('SELECT * FROM accounts ORDER BY createdAt DESC');
  }
  account(key: string): Account {
    const result = this.rows<Account>('SELECT * FROM accounts WHERE id=?',[id(key)])[0];
    if (!result) throw new Error('Conta não encontrada.');
    return result;
  }
  private assertLinks(parent: string | null, profile: string | null, self: string | null = null) {
    if (profile) this.profile(profile);
    let candidate = parent, remaining = this.listAccounts().length + 1;
    while (candidate) {
      if (candidate === self || --remaining < 0) throw new Error('Relação mãe/filha circular.');
      candidate = this.account(candidate).parentAccountId;
    }
  }
  createAccount(payload: unknown): Account {
    const input = accountInput(payload), uid = randomUUID(), now = new Date().toISOString();
    this.assertLinks(input.parentAccountId, input.profileId);
    const account: Account = { id: uid, ...input, createdAt: now, updatedAt: now };
    return this.mutate(() => {
      this.sql.run(
        'INSERT INTO accounts(id,provider,displayName,parentAccountId,profileId,status,createdAt,updatedAt) VALUES (?,?,?,?,?,?,?,?)',
        [uid,input.provider,input.displayName,input.parentAccountId,input.profileId,input.status,now,now],
      );
      this.audit('account.create','account',uid);
      return account;
    });
  }
  updateAccount(payload: unknown): Account {
    const patch = accountUpdate(payload), old = this.account(patch.id);
    const updated: Account = { ...old, ...patch, updatedAt: new Date().toISOString() };
    this.assertLinks(updated.parentAccountId, updated.profileId, updated.id);
    return this.mutate(() => {
      this.sql.run(
        'UPDATE accounts SET provider=?,displayName=?,parentAccountId=?,profileId=?,status=?,updatedAt=? WHERE id=?',
        [updated.provider,updated.displayName,updated.parentAccountId,
          updated.profileId,updated.status,updated.updatedAt,updated.id],
      );
      this.audit('account.update','account',updated.id);
      return updated;
    });
  }
  removeAccount(key: string) {
    this.account(key);
    this.mutate(() => {
      this.sql.run('DELETE FROM accounts WHERE id=?',[key]);
      this.audit('account.delete','account',key);
    });
  }
  importAccounts(payload: unknown): number {
    if (!Array.isArray(payload) || payload.length === 0 || payload.length > 200) {
      throw new Error('Importe de 1 a 200 contas por vez.');
    }
    const inputs = payload.map(accountInput);
    inputs.forEach(input => this.assertLinks(input.parentAccountId, input.profileId));
    return this.mutate(() => {
      const now = new Date().toISOString();
      for (const row of inputs) {
        this.sql.run(
          'INSERT INTO accounts(id,provider,displayName,parentAccountId,profileId,status,createdAt,updatedAt) VALUES (?,?,?,?,?,?,?,?)',
          [randomUUID(),row.provider,row.displayName,row.parentAccountId,row.profileId,row.status,now,now],
        );
      }
      this.audit('account.import','batch', String(inputs.length));
      return inputs.length;
    });
  }
  listAudit(): AuditEvent[] {
    return this.rows<AuditEvent>('SELECT * FROM audit_events ORDER BY id DESC LIMIT 150');
  }
}
