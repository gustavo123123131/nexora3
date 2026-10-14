import type { AccountInput, AccountStatus, AccountUpdate, ProfileInput, ProfileUpdate } from '../shared/contracts';

export function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Dados inválidos.');
  return value as Record<string, unknown>;
}
export function id(value: unknown): string {
  if (typeof value !== 'string' || !/^[a-f0-9-]{36}$/i.test(value)) throw new Error('Identificador inválido.');
  return value;
}
export function text(value: unknown, name: string, max = 120): string {
  if (typeof value !== 'string') throw new Error(name + ' deve ser texto.');
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > max || /[\x00-\x1f\x7f]/.test(trimmed)) {
    throw new Error(name + ' inválido (1 a ' + max + ' caracteres).');
  }
  return trimmed;
}
export function optionalId(value: unknown): string | null {
  return value === undefined || value === null || value === '' ? null : id(value);
}
export function status(value: unknown): AccountStatus {
  if (value === undefined) return 'draft';
  if (value === 'draft' || value === 'ready' || value === 'paused') return value;
  throw new Error('Estado de conta inválido.');
}
export function homeUrl(value: unknown): string {
  if (typeof value !== 'string' || value.length > 2048) throw new Error('URL inválida.');
  const raw = value.trim();
  if (!raw) return 'about:blank';
  if (/\s/.test(raw) || /[\\\x00-\x1f\x7f]/.test(raw)) throw new Error('URL contém caracteres inválidos.');
  const withScheme = /^[a-z][a-z\d+.-]*:\/\//i.test(raw) ? raw : 'https://' + raw;
  let url: URL;
  try { url = new URL(withScheme); } catch { throw new Error('URL inválida.'); }
  if (!['http:', 'https:'].includes(url.protocol) || !url.hostname ||
      url.username || url.password) throw new Error('Somente endereços HTTP/HTTPS sem credenciais são permitidos.');
  return url.toString();
}
export function accountInput(value: unknown): Required<AccountInput> {
  const d = record(value);
  return {
    provider: text(d.provider, 'Provedor', 80),
    displayName: text(d.displayName, 'Nome da conta', 120),
    parentAccountId: optionalId(d.parentAccountId),
    profileId: optionalId(d.profileId),
    status: status(d.status),
  };
}
export function accountUpdate(value: unknown): AccountUpdate {
  const d = record(value), result: AccountUpdate = { id: id(d.id) };
  if ('provider' in d) result.provider = text(d.provider, 'Provedor', 80);
  if ('displayName' in d) result.displayName = text(d.displayName, 'Nome da conta', 120);
  if ('parentAccountId' in d) result.parentAccountId = optionalId(d.parentAccountId);
  if ('profileId' in d) result.profileId = optionalId(d.profileId);
  if ('status' in d) result.status = status(d.status);
  return result;
}
export function profileInput(value: unknown): ProfileInput {
  const d = record(value);
  return { label: text(d.label, 'Nome do perfil', 80), homeUrl: homeUrl(d.homeUrl ?? '') };
}
export function profileUpdate(value: unknown): ProfileUpdate {
  const d = record(value), result: ProfileUpdate = { id: id(d.id) };
  if ('label' in d) result.label = text(d.label, 'Nome do perfil', 80);
  if ('homeUrl' in d) result.homeUrl = homeUrl(d.homeUrl);
  return result;
}
